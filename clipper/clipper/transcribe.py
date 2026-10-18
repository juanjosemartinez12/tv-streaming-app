from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

from .models import Segment, Transcript, Word


class TranscriptionError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load_model(name: str, device: str):
    from faster_whisper import WhisperModel

    try:
        # "auto" elige el tipo más rápido del equipo (int8 en CPU, float16/int8 en GPU).
        return WhisperModel(name, device=device, compute_type="auto")
    except Exception as exc:  # descarga (red, proxy) o carga (CUDA, disco) fallida
        raise TranscriptionError(
            f"No se pudo cargar el modelo de transcripción '{name}' ({exc}). La primera vez se descarga "
            "de Hugging Face: revisa tu conexión a internet."
        ) from exc


def transcribe(
    audio_path: Path,
    model_name: str,
    device: str = "auto",
    language: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Transcript:
    """Transcribe con faster-whisper y marcas de tiempo por palabra (necesarias para los subtítulos)."""
    model = _load_model(model_name, device)
    raw_segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    segments: list[Segment] = []
    for seg in raw_segments:
        words = [
            Word(text=w.word.strip(), start=round(w.start, 3), end=round(w.end, 3))
            for w in (seg.words or [])
            if w.word.strip()
        ]
        segments.append(Segment(start=round(seg.start, 3), end=round(seg.end, 3), text=seg.text.strip(), words=words))
        if on_progress and info.duration:
            on_progress(min(1.0, seg.end / info.duration))
    return Transcript(language=info.language, duration=info.duration, segments=segments)


def attach_energy(transcript: Transcript, energy: np.ndarray, window_s: float = 0.5) -> Transcript:
    for seg in transcript.segments:
        a, b = int(seg.start / window_s), max(int(seg.start / window_s) + 1, int(seg.end / window_s))
        chunk = energy[a:b]
        seg.energy = round(float(chunk.mean()), 3) if len(chunk) else 0.0
    return transcript
