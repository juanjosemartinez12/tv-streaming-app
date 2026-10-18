from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

DIRECT_MEDIA_EXTENSIONS = (".m3u8", ".mp4", ".mov", ".mkv", ".webm", ".ts", ".flv", ".mp3", ".m4a")


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"Falló {cmd[0]} ({proc.returncode}):\n{tail}")


def probe(path: Path) -> VideoInfo:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"No se pudo leer el video: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("El archivo no contiene una pista de video")
    num, _, den = video.get("avg_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 30.0
    width, height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return VideoInfo(
        width=width,
        height=height,
        duration=duration,
        fps=fps if 0 < fps < 240 else 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _rotation(stream: dict) -> int:
    rotate = stream.get("tags", {}).get("rotate")
    if rotate is None:
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotate = side["rotation"]
    try:
        return abs(int(float(rotate or 0))) % 360
    except ValueError:
        return 0


def is_direct_media_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(DIRECT_MEDIA_EXTENSIONS)


def download(url: str, dest_dir: Path, max_capture_s: int) -> tuple[Path, str]:
    """Descarga un video. Devuelve (ruta, título).

    Las URLs directas (mp4, HLS .m3u8 de un canal en vivo, etc.) se capturan con ffmpeg,
    limitando la duración para que una transmisión en vivo no se grabe para siempre.
    El resto (YouTube, Vimeo, ...) se descarga con yt-dlp.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if is_direct_media_url(url):
        out = dest_dir / "source.mp4"
        base = ["ffmpeg", "-y", "-v", "error", "-i", url, "-t", str(max_capture_s)]
        try:
            run([*base, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out)])
        except MediaError:
            run([*base, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out)])
        return out, Path(urlparse(url).path).stem or "video"

    import yt_dlp

    options = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp lanza muchos tipos distintos
        raise MediaError(f"No se pudo descargar el video: {exc}") from exc
    files = sorted(dest_dir.glob("source.*"))
    if not files:
        raise MediaError("La descarga no produjo ningún archivo")
    return files[0], str(info.get("title") or "video")


def extract_audio(video: Path, out: Path) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)])
    return out


def audio_energy(wav_path: Path, window_s: float = 0.5) -> np.ndarray:
    """Volumen RMS por ventana, normalizado a 0..1 (percentil 95 = 1)."""
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32)
    size = max(1, int(rate * window_s))
    usable = len(samples) // size * size
    if usable == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(samples[:usable].reshape(-1, size) ** 2, axis=1))
    ref = np.percentile(rms, 95) or 1.0
    return np.clip(rms / ref, 0, 1)


def thumbnail(video: Path, out: Path, at: float) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)])
    return out
