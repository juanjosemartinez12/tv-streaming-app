import pytest
from fastapi.testclient import TestClient

from clipper.api import create_app
from clipper.config import Settings
from clipper.jobs import JobStore
from clipper.pipeline import Pipeline

from .conftest import make_transcript
from .test_highlights import FakeClaude, raw_clip


def fake_transcriber(audio, model, device, language, on_progress):
    on_progress(1.0)
    return make_transcript()


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    claude = FakeClaude([raw_clip(0.5, 9.9), raw_clip(10.5, 17.4, gancho=10)])
    pipeline = Pipeline(JobStore(settings.jobs_dir), settings, claude_client=claude, transcriber=fake_transcriber)
    app = create_app(settings, pipeline)
    with TestClient(app) as test_client:
        yield test_client


def wait(client):
    assert client.app.state.worker.wait_idle(timeout=300)


def test_upload_process_rerender_and_delete(client, synthetic_video):
    video = synthetic_video(640, 360, duration=28)
    with open(video, "rb") as fh:
        res = client.post(
            "/api/jobs",
            files={"file": ("entrevista.mp4", fh, "video/mp4")},
            data={"num_clips": "2", "min_duration": "5", "max_duration": "15", "caption_style": "viral"},
        )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]
    wait(client)

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completado", job
    assert job["selector"] == "claude"
    assert [c["score"] for c in job["clips"]] == [80, 70]
    for clip in job["clips"]:
        assert clip["video"] and not clip["rendering"] and clip["version"] == 1
        media = client.get(f"/media/{job_id}/{clip['video']}")
        assert media.status_code == 200 and len(media.content) > 1000

    res = client.post(f"/api/jobs/{job_id}/clips/1/rerender", json={"caption_style": "clasico", "end": 16.0})
    assert res.status_code == 202, res.text
    assert res.json()["clips"][1]["rendering"] is True
    wait(client)
    clip = client.get(f"/api/jobs/{job_id}").json()["clips"][1]
    assert clip["caption_style"] == "clasico" and clip["end"] == 16.0 and clip["version"] == 2

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_clips_never_exceed_video_duration(client, synthetic_video):
    # La transcripción simulada dura ~26 s, pero el video solo 12 s, y Claude propone un corte hasta 30 s.
    client.app.state.pipeline.claude_client = FakeClaude([raw_clip(4.0, 30.0)])
    with open(synthetic_video(640, 360, duration=12), "rb") as fh:
        res = client.post("/api/jobs", files={"file": ("corto.mp4", fh, "video/mp4")},
                          data={"num_clips": "1", "min_duration": "5", "max_duration": "15"})
    job_id = res.json()["id"]
    wait(client)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completado", job
    assert all(c["end"] <= job["duration"] for c in job["clips"])
    res = client.post(f"/api/jobs/{job_id}/clips/0/rerender", json={"caption_style": "karaoke"})
    assert res.status_code == 202, res.text
    wait(client)


@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"data": {}}, 400),
        ({"data": {"url": "ftp://x"}}, 422),
        ({"data": {"url": "https://x.com/v.mp4", "min_duration": "60", "max_duration": "30"}}, 422),
        ({"data": {"url": "https://x.com/v.mp4", "layout": "cuadrado"}}, 422),
        ({"files": {"file": ("notas.txt", b"hola", "text/plain")}}, 415),
    ],
)
def test_rejects_invalid_requests(client, kwargs, status):
    assert client.post("/api/jobs", **kwargs).status_code == status
    assert client.get("/api/jobs").json() == []


def test_unknown_job_and_path_traversal(client):
    assert client.get("/api/jobs/noexiste").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404


def test_index_and_config(client):
    assert "Clipper" in client.get("/").text
    assert set(client.get("/api/config").json()) == {"claude", "model", "whisper"}
