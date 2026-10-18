import subprocess
from pathlib import Path

import pytest

from clipper.models import Segment, Transcript, Word

SENTENCES = [
    "¿Sabes cuál es el error más grande que comete todo el mundo?",
    "Nadie te lo dice pero es muy sencillo de evitar.",
    "Primero tienes que entender cómo funciona el dinero.",
    "Luego ahorras un poco cada semana sin falta.",
    "Y al final del año la diferencia es increíble.",
    "Eso fue todo por hoy, gracias por vernos.",
]


def make_transcript(sentences=SENTENCES, word_s: float = 0.4, pause_s: float = 0.6, energies=None) -> Transcript:
    t = 0.5
    segments = []
    for i, sentence in enumerate(sentences):
        words = []
        for token in sentence.split():
            words.append(Word(text=token, start=round(t, 3), end=round(t + word_s * 0.9, 3)))
            t += word_s
        energy = energies[i] if energies else 0.5
        segments.append(Segment(start=words[0].start, end=words[-1].end, text=sentence, words=words, energy=energy))
        t += pause_s
    return Transcript(language="es", duration=round(t + 0.5, 2), segments=segments)


@pytest.fixture
def transcript() -> Transcript:
    return make_transcript()


@pytest.fixture
def synthetic_video(tmp_path: Path):
    def build(width=1280, height=720, duration=12.0, audio=True, name="source.mp4") -> Path:
        out = tmp_path / name
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r=30:d={duration}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=f=330:d={duration}", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(cmd, check=True)
        return out

    return build
