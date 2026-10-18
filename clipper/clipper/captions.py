"""Subtítulos animados (ASS para quemar en el video) y SRT para subir a las plataformas."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import CaptionStyle, Word

WIDTH, HEIGHT = 1080, 1920


@dataclass(frozen=True)
class StyleSpec:
    size: int
    max_words: int
    max_chars: int
    uppercase: bool
    border_style: int  # 1 = contorno, 3 = caja opaca
    outline: int
    shadow: int
    primary: str  # colores ASS: &HAABBGGRR
    secondary: str
    back: str
    highlight: str | None = None


STYLES: dict[str, StyleSpec] = {
    # Estilo "Opus": pocas palabras, mayúsculas, la palabra actual resaltada y un poco más grande.
    "viral": StyleSpec(86, 3, 18, True, 1, 7, 3, "&H00FFFFFF", "&H00FFFFFF", "&H80000000", "&H000AD6FF"),
    # Relleno progresivo palabra por palabra.
    "karaoke": StyleSpec(76, 4, 22, True, 1, 6, 2, "&H0047E85C", "&H00FFFFFF", "&H80000000"),
    # Subtítulo tradicional sobre caja semitransparente.
    "clasico": StyleSpec(60, 7, 34, False, 3, 14, 0, "&H00FFFFFF", "&H00FFFFFF", "&H90000000"),
}

_STRIP_FOR_VIRAL = re.compile(r"[.,;:…]+$")
_BREAK_AFTER = re.compile(r"[.!?…]$")


def group_words(words: list[Word], max_words: int, max_chars: int, max_gap: float = 0.6) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current:
            chars = sum(len(w.text) + 1 for w in current) + len(word.text)
            prev = current[-1]
            if (
                len(current) >= max_words
                or chars > max_chars
                or word.start - prev.end > max_gap
                or _BREAK_AFTER.search(prev.text)
                or (prev.text.endswith(",") and len(current) >= 2)
            ):
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def shift_words(words: list[Word], start: float, end: float) -> list[Word]:
    """Palabras dentro de [start, end] con tiempos relativos al inicio del clip."""
    out = []
    for w in words:
        if w.end <= start or w.start >= end:
            continue
        out.append(Word(text=w.text, start=max(0.0, w.start - start), end=min(end, w.end) - start))
    return out


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean(text: str, spec: StyleSpec) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    if spec.uppercase:
        text = _STRIP_FOR_VIRAL.sub("", text).upper()
    return text


def _group_spans(groups: list[list[Word]]) -> list[tuple[float, float]]:
    """Inicio/fin visibles de cada grupo, rellenando huecos cortos para evitar parpadeos."""
    spans = []
    for i, group in enumerate(groups):
        start, end = group[0].start, group[-1].end + 0.25
        if i + 1 < len(groups):
            nxt = groups[i + 1][0].start
            end = nxt if nxt - group[-1].end < 0.5 else min(end, nxt)
        spans.append((start, max(end, start + 0.2)))
    return spans


def build_ass(words: list[Word], style: CaptionStyle, font: str, margin_v: int = 520) -> str:
    spec = STYLES[style]
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{spec.size},{spec.primary},{spec.secondary},&H00000000,{spec.back},"
        f"-1,0,0,0,100,100,0,0,{spec.border_style},{spec.outline},{spec.shadow},2,70,70,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    groups = group_words(words, spec.max_words, spec.max_chars)
    events = []
    for group, (g_start, g_end) in zip(groups, _group_spans(groups)):
        texts = [_clean(w.text, spec) for w in group]
        if style == "viral":
            for k, word in enumerate(group):
                start = g_start if k == 0 else word.start
                end = group[k + 1].start if k + 1 < len(group) else g_end
                if end <= start:
                    continue
                parts = [
                    f"{{\\c{spec.highlight}\\fscx112\\fscy112}}{t}{{\\r}}" if i == k else t
                    for i, t in enumerate(texts)
                ]
                events.append((start, end, " ".join(parts)))
        elif style == "karaoke":
            parts = []
            for k, word in enumerate(group):
                nxt = group[k + 1].start if k + 1 < len(group) else word.end
                parts.append(f"{{\\kf{max(1, int(round((nxt - word.start) * 100)))}}}{texts[k]}")
            events.append((g_start, g_end, " ".join(parts)))
        else:
            events.append((g_start, g_end, " ".join(texts)))
    lines = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{text}" for a, b, text in events]
    return "\n".join(header + lines) + "\n"


def build_srt(words: list[Word]) -> str:
    groups = group_words(words, max_words=8, max_chars=42)
    blocks = []
    for n, (group, (start, end)) in enumerate(zip(groups, _group_spans(groups)), start=1):
        text = " ".join(w.text for w in group)
        blocks.append(f"{n}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    return "\n".join(blocks)
