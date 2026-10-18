from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .models import Job

log = logging.getLogger(__name__)

ACTIVE_STATUSES = {"en_cola", "descargando", "transcribiendo", "analizando", "renderizando"}


class JobNotFound(KeyError):
    pass


class JobStore:
    """Guarda cada trabajo como JSON en su propia carpeta (data/jobs/<id>/job.json)."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def job_dir(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise JobNotFound(job_id)
        return self.root / job_id

    def create(self, job: Job) -> Job:
        self.job_dir(job.id).mkdir(parents=True, exist_ok=True)
        self.save(job)
        return job

    def save(self, job: Job) -> None:
        with self._lock:
            path = self.job_dir(job.id) / "job.json"
            if not path.parent.exists():
                raise JobNotFound(job.id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(path)

    def load(self, job_id: str) -> Job:
        path = self.job_dir(job_id) / "job.json"
        with self._lock:
            if not path.exists():
                raise JobNotFound(job_id)
            return Job.model_validate_json(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, mutate: Callable[[Job], None]) -> Job:
        with self._lock:
            job = self.load(job_id)
            mutate(job)
            self.save(job)
            return job

    def list(self) -> list[Job]:
        jobs = []
        for path in self.root.glob("*/job.json"):
            try:
                jobs.append(self.load(path.parent.name))
            except (JobNotFound, ValueError):
                log.warning("No se pudo leer %s", path)
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def delete(self, job_id: str) -> None:
        with self._lock:
            shutil.rmtree(self.job_dir(job_id), ignore_errors=True)


class Worker:
    """Procesa los trabajos de uno en uno en un hilo aparte (el video consume mucha CPU)."""

    def __init__(self, store: JobStore, handlers: dict[str, Callable[..., None]]):
        self.store = store
        self.handlers = handlers
        self.queue: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        for job in reversed(self.store.list()):
            if job.status in ACTIVE_STATUSES:
                self.store.update(job.id, lambda j: setattr(j, "message", "Reanudando tras reinicio…"))
                self.submit("process", job.id)
        self._thread = threading.Thread(target=self._loop, name="clipper-worker", daemon=True)
        self._thread.start()

    def submit(self, kind: str, *args) -> None:
        self.queue.put((kind, args))

    def wait_idle(self, timeout: float = 600) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.queue.unfinished_tasks == 0:
                return True
            time.sleep(0.05)
        return False

    def _loop(self) -> None:
        while True:
            kind, args = self.queue.get()
            try:
                self.handlers[kind](*args)
            except Exception:  # un trabajo roto no debe tumbar el hilo
                log.exception("Error inesperado en tarea %s%s", kind, args)
            finally:
                self.queue.task_done()
