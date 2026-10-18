from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Layout = Literal["fill", "fit"]
CaptionStyle = Literal["viral", "karaoke", "clasico", "ninguno"]
JobStatus = Literal[
    "en_cola", "descargando", "transcribiendo", "analizando", "renderizando", "completado", "error"
]


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    energy: float = 0.0  # 0..1, volumen relativo dentro del video


class Transcript(BaseModel):
    language: str = "es"
    duration: float
    segments: list[Segment]

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


class ScoreBreakdown(BaseModel):
    gancho: int = 0
    fluidez: int = 0
    valor: int = 0
    tendencia: int = 0


class ClipCandidate(BaseModel):
    start: float
    end: float
    title: str
    hook: str = ""
    reason: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    score: int = 0
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)

    @property
    def duration(self) -> float:
        return self.end - self.start


class Clip(ClipCandidate):
    index: int
    layout: Layout = "fill"
    caption_style: CaptionStyle = "viral"
    video: str | None = None  # rutas relativas a la carpeta del trabajo
    thumbnail: str | None = None
    srt: str | None = None
    version: int = 0  # sube con cada render; la web lo usa para no mostrar una versión en caché
    rendering: bool = False
    error: str | None = None


class JobOptions(BaseModel):
    num_clips: int = Field(default=5, ge=1, le=20)
    min_duration: float = Field(default=20, ge=5, le=180)
    max_duration: float = Field(default=60, ge=10, le=180)
    layout: Layout = "fill"
    caption_style: CaptionStyle = "viral"
    language: str | None = None  # None = detección automática
    instructions: str = ""  # p. ej. "solo momentos graciosos"


class Job(BaseModel):
    id: str
    created_at: float
    source_name: str
    source_url: str | None = None
    options: JobOptions = Field(default_factory=JobOptions)
    status: JobStatus = "en_cola"
    progress: float = 0
    message: str = "En cola"
    error: str | None = None
    warning: str | None = None
    duration: float | None = None
    language: str | None = None
    selector: str | None = None  # "claude" o "heuristica"
    clips: list[Clip] = Field(default_factory=list)
