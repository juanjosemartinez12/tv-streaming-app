"""Reencuadre inteligente 16:9 -> 9:16: sigue la cara principal y corta en seco en los cambios de plano."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ANALYSIS_WIDTH = 640
MIN_FACE_CONFIDENCE = 2.5


@dataclass
class Sample:
    t: float
    faces: list[tuple[float, float, float, float]]  # (centro_x, centro_y, ancho, alto) normalizados 0..1
    cut: bool = False  # True si hay cambio de plano respecto a la muestra anterior


def even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def crop_box(src_w: int, src_h: int, aspect: float = 9 / 16) -> tuple[int, int]:
    if src_w / src_h > aspect:
        return min(src_w, even(src_h * aspect)), src_h
    return src_w, min(src_h, even(src_w / aspect))


def analyze(video: Path, start: float, duration: float, src_w: int, src_h: int, sample_fps: float = 4.0) -> list[Sample]:
    import cv2

    width = min(ANALYSIS_WIDTH, src_w)
    height = even(src_h * width / src_w)
    proc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-vf", f"fps={sample_fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    frame_bytes = width * height * 3
    samples: list[Sample] = []
    prev_hist = None
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(height, width, 3)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        min_side = max(20, height // 18)
        rects, _, weights = detector.detectMultiScale3(
            gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side), outputRejectLevels=True
        )
        faces = []
        if len(rects):
            # Los falsos positivos de Haar tienen una confianza mucho menor que las caras reales.
            floor = max(MIN_FACE_CONFIDENCE, 0.6 * float(np.max(weights)))
            faces = [
                ((x + w / 2) / width, (y + h / 2) / height, w / width, h / height)
                for (x, y, w, h), weight in zip(rects, np.ravel(weights))
                if weight >= floor
            ]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < 0.6
        prev_hist = hist
        samples.append(Sample(t=len(samples) / sample_fps, faces=faces, cut=cut))
    proc.wait()
    return samples


def _pick_face(faces, previous: float | None) -> float | None:
    if not faces:
        return None
    largest = max(faces, key=lambda f: f[2] * f[3])
    if previous is not None:
        # Si la cara que seguíamos sigue ahí y es de tamaño similar, nos quedamos con ella.
        near = [f for f in faces if abs(f[0] - previous) < 0.12 and f[2] * f[3] >= 0.6 * largest[2] * largest[3]]
        if near:
            return min(near, key=lambda f: abs(f[0] - previous))[0]
    return largest[0]


def track(samples: list[Sample], dead_zone: float = 0.05, follow: float = 0.3) -> list[tuple[float, float, bool]]:
    """Devuelve (t, centro_x normalizado, inicia_plano) para cada muestra, con movimiento de cámara suave."""
    if not samples:
        return []
    shots: list[list[Sample]] = []
    for s in samples:
        if s.cut or not shots:
            shots.append([])
        shots[-1].append(s)

    result: list[tuple[float, float, bool]] = []
    for shot in shots:
        targets: list[float | None] = []
        prev = None
        for s in shot:
            prev = _pick_face(s.faces, prev) if s.faces else prev
            targets.append(prev if s.faces else None)
        known = [x for x in targets if x is not None]
        fallback = known[0] if known else 0.5
        filled: list[float] = []
        for x in targets:
            fallback = x if x is not None else fallback
            filled.append(fallback)

        camera = filled[0]
        positions = []
        for target in filled:
            if abs(target - camera) > dead_zone:
                camera += (target - camera) * follow
            positions.append(camera)
        # Suavizado final (media móvil centrada) para eliminar tirones.
        kernel = 5
        padded = np.pad(np.array(positions), kernel // 2, mode="edge")
        smooth = np.convolve(padded, np.ones(kernel) / kernel, mode="valid")
        for i, (s, x) in enumerate(zip(shot, smooth)):
            result.append((s.t, float(x), i == 0))
    return result


def crop_commands(path: list[tuple[float, float, bool]], src_w: int, crop_w: int, duration: float, fps: float = 30) -> tuple[int, str]:
    """Genera el archivo para el filtro `sendcmd` de ffmpeg que mueve el recorte cuadro a cuadro."""
    max_x = src_w - crop_w

    def to_x(center: float) -> int:
        return int(min(max_x, max(0, center * src_w - crop_w / 2)))

    if not path:
        return max_x // 2, ""
    times = [p[0] for p in path]
    lines = []
    last_x = None
    n_frames = int(duration * fps) + 1
    for f in range(n_frames):
        t = f / fps
        i = int(np.searchsorted(times, t, side="right")) - 1
        if i < 0:
            center = path[0][1]
        elif i + 1 < len(path) and not path[i + 1][2]:
            t0, x0, _ = path[i]
            t1, x1, _ = path[i + 1]
            center = x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        else:
            center = path[i][1]
        x = to_x(center)
        if x != last_x:
            lines.append(f"{t:.3f} crop x {x};")
            last_x = x
    return to_x(path[0][1]), "\n".join(lines) + "\n"
