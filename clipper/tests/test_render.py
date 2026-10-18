import pytest

from clipper import media, reframe, render

from .conftest import make_transcript


@pytest.mark.parametrize(
    "size,layout,style",
    [((1280, 720), "fill", "viral"), ((1280, 720), "fit", "karaoke"), ((720, 1280), "fill", "ninguno")],
)
def test_render_outputs_vertical_clip_with_audio(synthetic_video, tmp_path, size, layout, style):
    src = synthetic_video(*size, duration=8)
    info = media.probe(src)
    words = make_transcript().words
    files = render.render_clip(src, info, 1.0, 5.0, words, tmp_path / "out", layout, style, "Inter")

    out = media.probe(files["video"])
    assert (out.width, out.height) == (1080, 1920)
    assert out.duration == pytest.approx(4.0, abs=0.15)
    assert out.has_audio
    assert files["thumbnail"].stat().st_size > 0
    assert "-->" in files["srt"].read_text()
    assert (tmp_path / "out" / "subs.ass").exists() == (style != "ninguno")
    assert (tmp_path / "out" / "crop.cmd").exists() == (layout == "fill" and size[0] > size[1])


def test_render_without_audio_track(synthetic_video, tmp_path):
    src = synthetic_video(audio=False, duration=4)
    info = media.probe(src)
    assert not info.has_audio
    files = render.render_clip(src, info, 0.5, 3.0, [], tmp_path / "out", "fill", "viral", "Inter")
    assert media.probe(files["video"]).width == 1080


def test_analyze_samples_at_requested_rate(synthetic_video):
    src = synthetic_video(duration=5)
    samples = reframe.analyze(src, 1.0, 3.0, 1280, 720, sample_fps=4)
    assert 11 <= len(samples) <= 13
    assert samples[0].t == 0


def test_audio_energy_is_normalized(synthetic_video, tmp_path):
    wav = media.extract_audio(synthetic_video(duration=3), tmp_path / "a.wav")
    energy = media.audio_energy(wav)
    assert len(energy) == 6
    assert 0 < energy.max() <= 1
