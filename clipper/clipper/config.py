import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("CLIPPER_DATA_DIR", "./data")).resolve())
    claude_model: str = field(default_factory=lambda: _env("CLIPPER_MODEL", "claude-opus-5-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLIPPER_EFFORT", "high"))
    whisper_model: str = field(default_factory=lambda: _env("CLIPPER_WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: _env("CLIPPER_WHISPER_DEVICE", "auto"))
    max_live_capture_s: int = field(default_factory=lambda: int(_env("CLIPPER_MAX_CAPTURA", "600")))
    font: str = field(default_factory=lambda: _env("CLIPPER_FUENTE", "Inter"))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


def get_settings() -> Settings:
    return Settings()
