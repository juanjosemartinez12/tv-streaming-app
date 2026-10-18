import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from clipper import highlights
from clipper.config import Settings
from clipper.models import ClipCandidate, JobOptions

from .conftest import make_transcript


class FakeClaude:
    """Imita client.beta.messages.stream(...) del SDK de Anthropic."""

    def __init__(self, clips=None, stop_reason="end_turn", text=None):
        self.calls = []
        self._text = text if text is not None else json.dumps({"clips": clips or []})
        self._stop = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    @contextmanager
    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(stop_reason=self._stop, content=[SimpleNamespace(type="text", text=self._text)])
        yield SimpleNamespace(get_final_message=lambda: message)


def raw_clip(start, end, **scores):
    base = {"gancho": 20, "fluidez": 20, "valor": 20, "tendencia": 20}
    base.update(scores)
    return {
        "start": start, "end": end, "title": "Título", "hook": "Gancho", "description": "Desc",
        "reason": "Porque sí", "hashtags": ["#dinero", "ahorro "], **base,
    }


OPTIONS = JobOptions(num_clips=2, min_duration=5, max_duration=15)


def test_claude_request_uses_structured_output_and_fallbacks(transcript):
    client = FakeClaude([raw_clip(0.5, 9.0)])
    settings = Settings()
    clips, selector, warning = highlights.select_highlights(transcript, OPTIONS, settings, client)

    call = client.calls[0]
    assert call["model"] == settings.claude_model
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["schema"] == highlights.CLIPS_SCHEMA
    assert call["output_config"]["effort"] == settings.claude_effort
    prompt = call["messages"][0]["content"]
    assert "<transcripcion>" in prompt and "[0.5-" in prompt and "|E3]" in prompt

    assert selector == "claude" and warning is None
    assert clips[0].score == 80
    assert clips[0].hashtags == ["dinero", "ahorro"]


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_real_sdk_streams_request_and_parses_answer(transcript):
    """Pasa por el SDK real de Anthropic (red simulada) para validar la forma de la petición."""
    import anthropic
    import httpx2

    captured = {}
    answer = json.dumps({"clips": [raw_clip(0.5, 9.0)]})

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        body = _sse(
            {"type": "message_start", "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
             "usage": {"output_tokens": 50}},
            {"type": "message_stop"},
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    clips = highlights.select_with_claude(transcript, OPTIONS, Settings(), client)

    body = captured["body"]
    assert body["stream"] is True
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["headers"]["anthropic-beta"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body and "budget_tokens" not in json.dumps(body)
    assert clips[0].start == 0.5 and clips[0].score == 80


def test_scores_are_clamped_and_summed():
    cand = highlights._candidate_from_claude(raw_clip(1, 2, gancho=40, valor=-3))
    assert cand.breakdown.gancho == 25 and cand.breakdown.valor == 0
    assert cand.score == 25 + 20 + 0 + 20


@pytest.mark.parametrize("client", [FakeClaude(stop_reason="refusal"), FakeClaude(text="no es json")])
def test_falls_back_to_heuristic_when_claude_fails(transcript, client):
    clips, selector, warning = highlights.select_highlights(transcript, OPTIONS, Settings(), client)
    assert selector == "heuristica"
    assert "heurística" in warning
    assert clips


def test_heuristic_prefers_hooks_and_energy():
    t = make_transcript(energies=[0.9, 0.9, 0.2, 0.2, 0.2, 0.1])
    clips = highlights.refine(highlights.select_heuristic(t, OPTIONS), t, OPTIONS)
    assert clips[0].start <= t.segments[0].start + 0.01
    assert all(OPTIONS.min_duration * 0.6 <= c.duration <= OPTIONS.max_duration + 0.5 for c in clips)


def test_snap_never_cuts_words_and_completes_sentence(transcript):
    words = transcript.words
    # Corte a mitad de la segunda frase: debe extenderse hasta su punto final.
    second = transcript.segments[1]
    cand = ClipCandidate(start=transcript.segments[0].start + 0.05, end=second.words[3].start + 0.1, title="x")
    snapped = highlights.snap_to_words(cand, words, transcript.duration, JobOptions(min_duration=5, max_duration=30))
    assert snapped.start == pytest.approx(transcript.segments[0].start - 0.1)
    assert snapped.end == pytest.approx(second.end + 0.35)


def test_snap_trims_to_max_duration(transcript):
    cand = ClipCandidate(start=0, end=transcript.duration, title="todo")
    options = JobOptions(min_duration=5, max_duration=10)
    snapped = highlights.snap_to_words(cand, transcript.words, transcript.duration, options)
    assert snapped.duration <= 10 + 0.5


def test_refine_drops_overlaps_and_limits_count(transcript):
    segs = transcript.segments
    cands = [
        ClipCandidate(start=segs[0].start, end=segs[1].end, title="a", score=90),
        ClipCandidate(start=segs[0].start + 1, end=segs[1].end, title="solapado", score=80),
        ClipCandidate(start=segs[2].start, end=segs[3].end, title="b", score=70),
        ClipCandidate(start=segs[4].start, end=segs[5].end, title="c", score=60),
    ]
    out = highlights.refine(cands, transcript, OPTIONS)
    assert [c.title for c in out] == ["a", "b"]


def test_no_speech_is_an_error():
    empty = make_transcript(sentences=[])
    with pytest.raises(highlights.HighlightError):
        highlights.select_highlights(empty, OPTIONS, Settings(), FakeClaude())
