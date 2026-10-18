from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .config import get_settings
from .jobs import JobStore
from .models import Job, JobOptions
from .pipeline import Pipeline


def _slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text.lower()).strip("-")[:50] or "clip"


def cmd_process(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = JobStore(settings.jobs_dir)
    source = args.fuente
    is_url = source.startswith(("http://", "https://"))
    if not is_url and not Path(source).is_file():
        print(f"No existe el archivo: {source}", file=sys.stderr)
        return 1

    try:
        options = JobOptions(
            num_clips=args.clips, min_duration=args.min, max_duration=args.max, layout=args.formato,
            caption_style=args.subtitulos, language=args.idioma, instructions=args.indicaciones or "",
        )
    except ValidationError as exc:
        for err in exc.errors():
            print(f"Opción inválida ({err['loc'][0]}): {err['msg']}", file=sys.stderr)
        return 2
    if options.min_duration >= options.max_duration:
        print("La duración mínima (--min) debe ser menor que la máxima (--max)", file=sys.stderr)
        return 2
    job = Job(id=store.new_id(), created_at=time.time(), options=options,
              source_name=source if is_url else Path(source).name, source_url=source if is_url else None)
    store.create(job)
    if not is_url:
        shutil.copy(source, store.job_dir(job.id) / f"source{Path(source).suffix.lower()}")

    last = ""

    def report(j: Job) -> None:
        nonlocal last
        line = f"[{j.progress:5.1f}%] {j.message}"
        if line != last:
            print(line, flush=True)
            last = line

    Pipeline(store, settings, on_update=report).process(job.id)
    job = store.load(job.id)
    if job.status != "completado":
        print(f"Error: {job.error}", file=sys.stderr)
        return 1

    out_dir = Path(args.salida) if args.salida else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nSelección hecha con: {job.selector}\n")
    for clip in job.clips:
        print(f"#{clip.index + 1}  {clip.score:3d}/100  {clip.start:7.1f}s → {clip.end:7.1f}s  {clip.title}")
        if clip.reason:
            print(f"      {clip.reason}")
        if clip.video and out_dir:
            base = f"{clip.index + 1:02d}-{_slug(clip.title)}"
            job_dir = store.job_dir(job.id)
            shutil.copy(job_dir / clip.video, out_dir / f"{base}.mp4")
            if clip.srt:
                shutil.copy(job_dir / clip.srt, out_dir / f"{base}.srt")
    print(f"\nArchivos en: {out_dir or store.job_dir(job.id)}")
    return 0


def cmd_server(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.puerto)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="clipper", description="Convierte videos largos en clips verticales con IA.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("procesar", help="Procesa un video (archivo o enlace) desde la terminal")
    p.add_argument("fuente", help="Ruta a un video, enlace de YouTube o URL directa (.mp4, .m3u8)")
    p.add_argument("--clips", type=int, default=5, help="Cuántos clips generar (1-20)")
    p.add_argument("--min", type=float, default=20, help="Duración mínima en segundos")
    p.add_argument("--max", type=float, default=60, help="Duración máxima en segundos")
    p.add_argument("--formato", choices=["fill", "fit"], default="fill",
                   help="fill = recorte vertical siguiendo caras; fit = video completo con fondo difuminado")
    p.add_argument("--subtitulos", choices=["viral", "karaoke", "clasico", "ninguno"], default="viral")
    p.add_argument("--idioma", default=None, help="Código de idioma (es, en, ...). Por defecto se detecta solo")
    p.add_argument("--indicaciones", default="", help='Ej.: "solo los momentos más graciosos"')
    p.add_argument("--salida", default=None, help="Carpeta donde copiar los clips terminados")
    p.set_defaults(func=cmd_process)

    s = sub.add_parser("servidor", help="Inicia la aplicación web")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--puerto", type=int, default=8000)
    s.set_defaults(func=cmd_server)

    args = parser.parse_args(argv)
    return args.func(args)
