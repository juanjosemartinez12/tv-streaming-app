from __future__ import annotations

from pathlib import Path

from . import captions, media, reframe
from .models import CaptionStyle, Layout, Word

OUT_W, OUT_H = captions.WIDTH, captions.HEIGHT


def video_filter(layout: Layout, info: media.VideoInfo, source: Path, start: float, duration: float, workdir: Path) -> str:
    """Cadena de filtros de video (sin subtítulos) que deja el cuadro en 1080x1920."""
    if layout == "fit":
        return (
            f"split=2[bg][fg];"
            f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            f"boxblur=24:2,eq=brightness=-0.12[bgb];"
            f"[fg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    crop_w, crop_h = reframe.crop_box(info.width, info.height)
    scale = f"scale={OUT_W}:{OUT_H}:flags=lanczos"
    if crop_w < info.width:
        samples = reframe.analyze(source, start, duration, info.width, info.height)
        path = reframe.track(samples)
        x0, commands = reframe.crop_commands(path, info.width, crop_w, duration)
        if commands:
            (workdir / "crop.cmd").write_text(commands)
            return f"sendcmd=f=crop.cmd,crop=w={crop_w}:h={crop_h}:x={x0}:y=0,{scale}"
        return f"crop=w={crop_w}:h={crop_h}:x={x0}:y=0,{scale}"
    if crop_h < info.height:
        return f"crop=w={crop_w}:h={crop_h}:x=0:y={(info.height - crop_h) // 2},{scale}"
    return scale


def render_clip(
    source: Path,
    info: media.VideoInfo,
    start: float,
    end: float,
    words: list[Word],
    workdir: Path,
    layout: Layout,
    caption_style: CaptionStyle,
    font: str,
    output_name: str = "clip.mp4",
) -> dict[str, Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    duration = end - start
    clip_words = captions.shift_words(words, start, end)

    srt_path = workdir / "subs.srt"
    srt_path.write_text(captions.build_srt(clip_words), encoding="utf-8")

    vf = video_filter(layout, info, source, start, duration, workdir)
    if caption_style != "ninguno" and clip_words:
        margin = 470 if layout == "fit" else 520
        (workdir / "subs.ass").write_text(captions.build_ass(clip_words, caption_style, font, margin), encoding="utf-8")
        vf += ",ass=subs.ass"
    graph = f"[0:v]{vf},setsar=1,format=yuv420p[v]"
    maps = ["-map", "[v]"]
    if info.has_audio:
        # -14 LUFS: el volumen objetivo de TikTok, Reels y Shorts.
        graph += ";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]

    out = workdir / output_name
    media.run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-filter_complex", graph, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
            "-movflags", "+faststart", output_name,
        ],
        cwd=workdir,
    )
    thumb = media.thumbnail(out, workdir / output_name.replace(".mp4", ".jpg"), at=min(1.5, duration / 3))
    return {"video": out, "thumbnail": thumb, "srt": srt_path}
