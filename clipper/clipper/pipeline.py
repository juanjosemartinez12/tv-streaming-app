"""Flujo completo: fuente -> transcripción -> selección de momentos -> render de cada clip."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import media, render
from .config import Settings
from .highlights import select_highlights
from .jobs import JobNotFound, JobStore
from .models import CaptionStyle, Clip, Job, Layout, Transcript
from .transcribe import attach_energy, transcribe

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store: JobStore, settings: Settings, claude_client=None, transcriber=transcribe,
                 on_update: Callable[[Job], None] | None = None):
        self.store = store
        self.settings = settings
        self.claude_client = claude_client
        self.transcriber = transcriber
        self.on_update = on_update

    def _set(self, job_id: str, **fields) -> Job:
        def mutate(job: Job) -> None:
            for k, v in fields.items():
                setattr(job, k, v)

        job = self.store.update(job_id, mutate)
        if self.on_update:
            self.on_update(job)
        return job

    def process(self, job_id: str) -> None:
        try:
            self._process(job_id)
        except JobNotFound:
            log.info("El trabajo %s fue eliminado durante el proceso", job_id)
        except Exception as exc:
            log.exception("Falló el trabajo %s", job_id)
            try:
                self._set(job_id, status="error", error=str(exc), message="Ocurrió un error")
            except JobNotFound:
                pass

    def _process(self, job_id: str) -> None:
        job = self.store.load(job_id)
        job_dir = self.store.job_dir(job_id)

        source = self._ensure_source(job, job_dir)
        info = media.probe(source)
        self._set(job_id, duration=round(info.duration, 2))
        if not info.has_audio:
            raise media.MediaError("El video no tiene audio; se necesita voz para encontrar los mejores momentos.")

        transcript = self._ensure_transcript(job, job_dir, source)
        # La pista de audio puede durar más que la de video; los cortes nunca deben pasarse del video.
        transcript.duration = min(transcript.duration, info.duration) if info.duration else transcript.duration
        self._set(job_id, status="analizando", progress=55, language=transcript.language,
                  message="Buscando los momentos con más potencial viral…")

        candidates, selector, warning = select_highlights(transcript, job.options, self.settings, self.claude_client)
        clips = [
            Clip(index=i, layout=job.options.layout, caption_style=job.options.caption_style, rendering=True,
                 **c.model_dump())
            for i, c in enumerate(candidates)
        ]
        if not clips:
            raise media.MediaError("No se encontraron fragmentos que cumplan la duración pedida.")
        self._set(job_id, selector=selector, clips=clips, warning=warning, status="renderizando", progress=60,
                  message=f"Generando {_plural(len(clips))}…")

        for n, clip in enumerate(clips, start=1):
            self._render(job_id, clip.index, source, info, transcript)
            self._set(job_id, progress=round(60 + 40 * n / len(clips), 1), message=f"Clip {n} de {len(clips)} listo")

        done = "1 clip listo" if len(clips) == 1 else f"{len(clips)} clips listos"
        self._set(job_id, status="completado", progress=100, message=done)

    def _ensure_source(self, job: Job, job_dir: Path) -> Path:
        existing = sorted(p for p in job_dir.glob("source.*") if p.suffix not in (".part", ".ytdl"))
        if existing:
            return existing[0]
        if not job.source_url:
            raise media.MediaError("No hay video para procesar")
        self._set(job.id, status="descargando", progress=2, message="Descargando video…")
        path, title = media.download(job.source_url, job_dir, self.settings.max_live_capture_s)
        if job.source_name == job.source_url:
            self._set(job.id, source_name=title)
        return path

    def _ensure_transcript(self, job: Job, job_dir: Path, source: Path) -> Transcript:
        cached = job_dir / "transcript.json"
        if cached.exists():
            return Transcript.model_validate_json(cached.read_text(encoding="utf-8"))
        self._set(job.id, status="transcribiendo", progress=10, message="Extrayendo audio…")
        audio = media.extract_audio(source, job_dir / "audio.wav")
        energy = media.audio_energy(audio)

        def on_progress(fraction: float) -> None:
            self._set(job.id, progress=round(12 + 40 * fraction, 1), message=f"Transcribiendo… {fraction:.0%}")

        self._set(job.id, progress=12, message="Transcribiendo…")
        transcript = self.transcriber(
            audio, self.settings.whisper_model, self.settings.whisper_device, job.options.language, on_progress
        )
        transcript = attach_energy(transcript, energy)
        cached.write_text(transcript.model_dump_json(), encoding="utf-8")
        audio.unlink(missing_ok=True)
        return transcript

    def _render(self, job_id: str, index: int, source: Path, info: media.VideoInfo, transcript: Transcript) -> None:
        clip = self.store.load(job_id).clips[index]
        workdir = self.store.job_dir(job_id) / "clips" / str(index)
        try:
            files = render.render_clip(
                source, info, clip.start, clip.end, transcript.words, workdir,
                clip.layout, clip.caption_style, self.settings.font,
            )
        except media.MediaError as exc:
            log.warning("No se pudo renderizar el clip %s/%s: %s", job_id, index, exc)
            self._update_clip(job_id, index, rendering=False, error=str(exc))
            return
        rel = {k: str(v.relative_to(self.store.job_dir(job_id))) for k, v in files.items()}
        self._update_clip(job_id, index, rendering=False, error=None, version=clip.version + 1, **rel)

    def _update_clip(self, job_id: str, index: int, **fields) -> None:
        def mutate(job: Job) -> None:
            for k, v in fields.items():
                setattr(job.clips[index], k, v)

        self.store.update(job_id, mutate)

    def rerender(self, job_id: str, index: int, start: float | None = None, end: float | None = None,
                 layout: Layout | None = None, caption_style: CaptionStyle | None = None) -> None:
        try:
            job = self.store.load(job_id)
            job_dir = self.store.job_dir(job_id)
            source = self._ensure_source(job, job_dir)
            info = media.probe(source)
            transcript = Transcript.model_validate_json((job_dir / "transcript.json").read_text(encoding="utf-8"))
            changes = {"start": start, "end": end, "layout": layout, "caption_style": caption_style}
            self._update_clip(job_id, index, **{k: v for k, v in changes.items() if v is not None})
            self._render(job_id, index, source, info, transcript)
        except JobNotFound:
            log.info("El trabajo %s fue eliminado antes de re-renderizar", job_id)
        except Exception as exc:
            log.exception("Falló el re-render de %s/%s", job_id, index)
            self._update_clip(job_id, index, rendering=False, error=str(exc))


def _plural(n: int) -> str:
    return f"{n} clip" if n == 1 else f"{n} clips"
