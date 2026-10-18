from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import Settings, get_settings
from .highlights import claude_available
from .jobs import ACTIVE_STATUSES, JobNotFound, JobStore, Worker
from .models import CaptionStyle, Job, JobOptions, Layout
from .pipeline import Pipeline

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_UPLOADS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts", ".flv"}


class RerenderRequest(BaseModel):
    start: float | None = None
    end: float | None = None
    layout: Layout | None = None
    caption_style: CaptionStyle | None = None


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = pipeline.store if pipeline else JobStore(settings.jobs_dir)
    pipeline = pipeline or Pipeline(store, settings)
    worker = Worker(store, {"process": pipeline.process, "rerender": pipeline.rerender})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.start()
        yield

    app = FastAPI(title="Clipper IA", lifespan=lifespan)
    app.state.worker = worker
    app.state.store = store
    app.state.pipeline = pipeline
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/media", StaticFiles(directory=store.root), name="media")

    def get_job(job_id: str) -> Job:
        try:
            return store.load(job_id)
        except JobNotFound:
            raise HTTPException(404, "Trabajo no encontrado") from None

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/config")
    def config() -> dict:
        return {"claude": claude_available(), "model": settings.claude_model, "whisper": settings.whisper_model}

    @app.post("/api/jobs", status_code=201)
    async def create_job(
        file: UploadFile | None = File(None),
        url: str = Form(""),
        num_clips: int = Form(5),
        min_duration: float = Form(20),
        max_duration: float = Form(60),
        layout: str = Form("fill"),
        caption_style: str = Form("viral"),
        language: str = Form(""),
        instructions: str = Form(""),
    ) -> Job:
        url = url.strip()
        if not url and (file is None or not file.filename):
            raise HTTPException(400, "Sube un video o pega un enlace")
        try:
            options = JobOptions(
                num_clips=num_clips, min_duration=min_duration, max_duration=max_duration, layout=layout,
                caption_style=caption_style, language=language.strip() or None, instructions=instructions[:500],
            )
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from None
        if options.min_duration >= options.max_duration:
            raise HTTPException(422, "La duración mínima debe ser menor que la máxima")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(422, "El enlace debe empezar con http:// o https://")
        suffix = "" if url else Path(file.filename).suffix.lower()
        if not url and suffix not in ALLOWED_UPLOADS:
            raise HTTPException(415, f"Formato no soportado ({suffix or 'sin extensión'})")

        job = Job(
            id=store.new_id(), created_at=time.time(), options=options,
            source_name=url or Path(file.filename).name, source_url=url or None,
        )
        store.create(job)
        if not url:
            with open(store.job_dir(job.id) / f"source{suffix}", "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)
        worker.submit("process", job.id)
        return job

    @app.get("/api/jobs")
    def list_jobs() -> list[Job]:
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def read_job(job_id: str) -> Job:
        return get_job(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> None:
        job = get_job(job_id)
        if job.status in ACTIVE_STATUSES or any(c.rendering for c in job.clips):
            raise HTTPException(409, "Espera a que termine el proceso para eliminarlo")
        store.delete(job_id)

    @app.post("/api/jobs/{job_id}/clips/{index}/rerender", status_code=202)
    def rerender(job_id: str, index: int, body: RerenderRequest) -> Job:
        job = get_job(job_id)
        if job.status != "completado":
            raise HTTPException(409, "El trabajo todavía se está procesando")
        if not 0 <= index < len(job.clips):
            raise HTTPException(404, "Clip no encontrado")
        clip = job.clips[index]
        if clip.rendering:
            raise HTTPException(409, "Este clip ya se está generando")
        start = clip.start if body.start is None else body.start
        end = clip.end if body.end is None else body.end
        if not (0 <= start < end <= (job.duration or end) + 0.5) or end - start < 3 or end - start > 180:
            raise HTTPException(422, "Rango de tiempo inválido (entre 3 y 180 segundos dentro del video)")

        def mark(j: Job) -> None:
            j.clips[index].rendering = True
            j.clips[index].error = None

        job = store.update(job_id, mark)
        worker.submit("rerender", job_id, index, body.start, body.end, body.layout, body.caption_style)
        return job

    return app
