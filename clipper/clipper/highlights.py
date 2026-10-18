"""Selección de los mejores momentos: Claude (por defecto) o una heurística local sin IA."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path

from .config import Settings
from .models import ClipCandidate, JobOptions, ScoreBreakdown, Transcript, Word

log = logging.getLogger(__name__)

# Modelos que aceptan `fallbacks: "default"` (reintento automático en otro modelo si hay rechazo).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

SYSTEM_PROMPT = """\
Eres un editor experto en video corto (TikTok, Reels, YouTube Shorts) que convierte podcasts, \
entrevistas, transmisiones y clases en clips virales. Recibes la transcripción completa de un video \
con marcas de tiempo y eliges los fragmentos con más potencial para funcionar solos en redes.

Un buen clip:
- Engancha en los primeros 3 segundos: arranca con una afirmación fuerte, una pregunta, un dato \
sorprendente o un conflicto; nunca con saludos, muletillas ni contexto de relleno.
- Se entiende sin ver el resto del video: tiene planteamiento y remate, y no depende de algo dicho \
antes ("como te decía", "eso que mencionaste").
- Termina en un cierre natural (remate, conclusión, risa, frase redonda), no a mitad de una idea.
- Aporta algo: emoción, humor, polémica, una enseñanza práctica, una historia o una opinión clara.

La puntuación de viralidad es la suma de cuatro criterios, cada uno de 0 a 25:
- gancho: fuerza de los primeros segundos.
- fluidez: coherencia y cierre limpio, sin cortes bruscos.
- valor: utilidad, emoción o entretenimiento para alguien que no conoce el video.
- tendencia: afinidad con los temas y formatos que hoy funcionan en redes.
Sé exigente y usa todo el rango; reserva los totales de 85 o más para momentos excepcionales.

Sobre los tiempos:
- Cada línea de la transcripción es `[inicio-fin|E#] texto`, con tiempos en segundos. Da `start` y \
`end` en segundos usando esos tiempos; prefiere empezar al inicio de una línea y terminar al final \
de otra. El corte se ajusta después a la palabra más cercana.
- E1-E5 es el volumen relativo del audio (E5 = más intenso): una pista de emoción, risas o tensión.
- Respeta la duración mínima y máxima pedidas y no solapes clips entre sí.
- Ordena los clips de mayor a menor puntuación.

Escribe title, hook, description, reason y hashtags en el idioma del video. El título es corto \
(máximo 70 caracteres) y llamativo, sin clickbait engañoso. `hook` es un texto de gancho para \
mostrar en pantalla. `description` es un texto breve para publicar junto al clip. `reason` explica \
en una o dos frases por qué el clip puede funcionar. Da de 3 a 6 hashtags sin el símbolo #."""

_CLIP_PROPERTIES = {
    "start": {"type": "number"},
    "end": {"type": "number"},
    "title": {"type": "string"},
    "hook": {"type": "string"},
    "description": {"type": "string"},
    "reason": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "gancho": {"type": "integer"},
    "fluidez": {"type": "integer"},
    "valor": {"type": "integer"},
    "tendencia": {"type": "integer"},
}

CLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _CLIP_PROPERTIES,
                "required": list(_CLIP_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


class HighlightError(RuntimeError):
    pass


def format_transcript(transcript: Transcript) -> str:
    lines = []
    for seg in transcript.segments:
        level = 1 + min(4, int(seg.energy * 5))
        lines.append(f"[{seg.start:.1f}-{seg.end:.1f}|E{level}] {seg.text}")
    return "\n".join(lines)


def build_user_prompt(transcript: Transcript, options: JobOptions, wanted: int) -> str:
    parts = [
        f"Duración del video: {transcript.duration:.0f} s. Idioma detectado: {transcript.language}.",
        f"Encuentra los {wanted} mejores clips de entre {options.min_duration:.0f} y "
        f"{options.max_duration:.0f} segundos.",
    ]
    if options.instructions.strip():
        parts.append(f"Preferencias del usuario para elegir los clips: {options.instructions.strip()}")
    parts.append(f"<transcripcion>\n{format_transcript(transcript)}\n</transcripcion>")
    return "\n\n".join(parts)


def claude_available() -> bool:
    if os.environ.get("CLIPPER_SELECTOR", "auto") == "heuristica":
        return False
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def select_with_claude(transcript: Transcript, options: JobOptions, settings: Settings, client=None) -> list[ClipCandidate]:
    import anthropic

    client = client or anthropic.Anthropic()
    wanted = min(options.num_clips + 3, options.num_clips * 2)
    extra = {}
    if settings.claude_model in FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    with client.beta.messages.stream(
        model=settings.claude_model,
        max_tokens=32000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(transcript, options, wanted)}],
        output_config={
            "effort": settings.claude_effort,
            "format": {"type": "json_schema", "schema": CLIPS_SCHEMA},
        },
        **extra,
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise HighlightError("Claude no aceptó analizar este video")
    if message.stop_reason == "max_tokens":
        raise HighlightError("La respuesta de Claude quedó incompleta")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        raw_clips = json.loads(text)["clips"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HighlightError("Claude devolvió una respuesta con formato inesperado") from exc
    return [_candidate_from_claude(c) for c in raw_clips]


def _candidate_from_claude(raw: dict) -> ClipCandidate:
    parts = {k: max(0, min(25, int(raw.get(k, 0)))) for k in ("gancho", "fluidez", "valor", "tendencia")}
    return ClipCandidate(
        start=float(raw["start"]),
        end=float(raw["end"]),
        title=raw.get("title", "").strip()[:100],
        hook=raw.get("hook", "").strip(),
        description=raw.get("description", "").strip(),
        reason=raw.get("reason", "").strip(),
        hashtags=[h.strip().lstrip("#").replace(" ", "") for h in raw.get("hashtags", []) if h.strip()][:8],
        score=sum(parts.values()),
        breakdown=ScoreBreakdown(**parts),
    )


# --- Heurística local (sin IA) -------------------------------------------------------------

HOOK_WORDS = {
    "secreto", "nadie", "nunca", "siempre", "increíble", "error", "errores", "verdad", "mentira",
    "importante", "problema", "dinero", "miedo", "jamás", "imagina", "atención", "cuidado", "truco",
    "consejo", "historia", "pasó", "realmente", "peor", "mejor", "clave", "por qué", "cómo",
    "secret", "never", "always", "nobody", "mistake", "truth", "money", "why", "how", "crazy", "best", "worst",
}
STOPWORDS = {
    "que", "de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "es", "por", "con", "no", "se",
    "lo", "para", "pero", "como", "más", "mas", "muy", "eso", "esto", "este", "esta", "porque", "cuando",
    "también", "entonces", "bueno", "pues", "todo", "todos", "hay", "tiene", "tengo", "estaba", "están",
    "fue", "ser", "son", "era", "the", "and", "that", "this", "with", "have", "you", "they", "what",
    "about", "there", "their", "would", "really", "because", "people", "something",
}
_SENTENCE_END = re.compile(r"[.!?…]$")


def select_heuristic(transcript: Transcript, options: JobOptions) -> list[ClipCandidate]:
    segs = transcript.segments
    windows: list[ClipCandidate] = []
    for i in range(len(segs)):
        for j in range(i, len(segs)):
            duration = segs[j].end - segs[i].start
            if duration > options.max_duration:
                break
            if duration >= options.min_duration:
                windows.append(_score_window(segs[i : j + 1]))
    if not windows and segs:
        windows.append(_score_window(segs))
    return sorted(windows, key=lambda c: c.score, reverse=True)


def _score_window(segs) -> ClipCandidate:
    text = " ".join(s.text for s in segs).strip()
    lowered = text.lower()
    first = segs[0].text.lower()
    duration = max(0.1, segs[-1].end - segs[0].start)
    word_count = len(text.split())

    gancho = 8 + 9 * ("?" in first or "¿" in first) + 8 * any(w in first for w in HOOK_WORDS)
    fluidez = 10 + 10 * bool(_SENTENCE_END.search(segs[-1].text.strip())) + min(5, int(word_count / duration * 2))
    valor = min(25, 6 + 3 * sum(lowered.count(w) for w in HOOK_WORDS) + 2 * (lowered.count("!") + lowered.count("?")))
    energy = sum(s.energy * (s.end - s.start) for s in segs) / duration
    tendencia = min(25, int(5 + energy * 20))
    breakdown = ScoreBreakdown(gancho=min(25, gancho), fluidez=min(25, fluidez), valor=valor, tendencia=tendencia)

    first_sentence = re.split(r"(?<=[.!?…])\s", segs[0].text.strip())[0]
    counts = Counter(w for w in re.findall(r"\w{5,}", lowered) if w not in STOPWORDS)
    return ClipCandidate(
        start=segs[0].start,
        end=segs[-1].end,
        title=_truncate(first_sentence, 70),
        hook=_truncate(first_sentence, 90),
        description=_truncate(text, 220),
        reason="Elegido por la heurística local (volumen, preguntas y palabras gancho).",
        hashtags=[w for w, _ in counts.most_common(4)],
        score=breakdown.gancho + breakdown.fluidez + breakdown.valor + breakdown.tendencia,
        breakdown=breakdown,
    )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# --- Ajuste final de cortes ----------------------------------------------------------------


def snap_to_words(candidate: ClipCandidate, words: list[Word], duration: float, options: JobOptions) -> ClipCandidate | None:
    """Ajusta inicio/fin para no cortar palabras, cerrar la frase y respetar la duración pedida."""
    start, end = max(0.0, candidate.start), min(duration, candidate.end)
    if end <= start:
        return None
    inside = [w for w in words if w.end > start - 0.25 and w.start < end + 0.25]
    if inside:
        start, end = inside[0].start, inside[-1].end
        if not _SENTENCE_END.search(inside[-1].text):
            # Si la frase termina un poco después, la incluimos completa.
            last_idx = words.index(inside[-1])
            prev_end = end
            for w in words[last_idx + 1 : last_idx + 12]:
                if w.end - start > options.max_duration or w.start - prev_end > 2.5:
                    break
                prev_end = w.end
                if _SENTENCE_END.search(w.text):
                    end = w.end
                    break
        if end - start > options.max_duration:
            fitting = [w for w in words if w.start >= start and w.end <= start + options.max_duration]
            ends = [w for w in fitting if _SENTENCE_END.search(w.text)] or fitting
            end = ends[-1].end if ends else start + options.max_duration
        if end - start < options.min_duration:
            following = [w for w in words if w.start >= end]
            for w in following:
                if w.end - start > options.max_duration:
                    break
                end = w.end
                if end - start >= options.min_duration and _SENTENCE_END.search(w.text):
                    break
    start = max(0.0, start - 0.1)
    end = min(duration, end + 0.35)
    if end - start < min(options.min_duration, duration) * 0.6:
        return None
    return candidate.model_copy(update={"start": round(start, 2), "end": round(end, 2)})


def refine(candidates: list[ClipCandidate], transcript: Transcript, options: JobOptions) -> list[ClipCandidate]:
    words = transcript.words
    chosen: list[ClipCandidate] = []
    for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
        snapped = snap_to_words(cand, words, transcript.duration, options)
        if snapped is None:
            continue
        if any(_overlap(snapped, other) > 0.25 * min(snapped.duration, other.duration) for other in chosen):
            continue
        chosen.append(snapped)
        if len(chosen) == options.num_clips:
            break
    return chosen


def _overlap(a: ClipCandidate, b: ClipCandidate) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def select_highlights(transcript: Transcript, options: JobOptions, settings: Settings, client=None) -> tuple[list[ClipCandidate], str, str | None]:
    """Devuelve (clips, selector usado, aviso). Si Claude falla, usa la heurística y lo avisa."""
    if not transcript.segments:
        raise HighlightError("No se detectó voz en el video; se necesita diálogo para encontrar momentos.")
    warning = None
    if client is not None or claude_available():
        import anthropic

        try:
            clips = refine(select_with_claude(transcript, options, settings, client), transcript, options)
            if clips:
                return clips, "claude", None
            warning = "Claude no propuso clips válidos; se usó la heurística local."
        except (anthropic.AnthropicError, HighlightError) as exc:
            log.warning("Fallo al usar Claude: %s", exc)
            warning = f"No se pudo usar Claude ({exc}); se usó la heurística local."
    return refine(select_heuristic(transcript, options), transcript, options), "heuristica", warning
