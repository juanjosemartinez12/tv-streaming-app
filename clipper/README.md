# Clipper IA

Un sistema tipo **Opus Clip**: le das un video largo (podcast, entrevista, clase, transmisión en vivo) y te devuelve los mejores momentos convertidos en **clips verticales 9:16** listos para TikTok, Reels y YouTube Shorts.

Qué hace con cada video:

1. **Transcribe** el audio con marcas de tiempo por palabra ([faster-whisper](https://github.com/SYSTRAN/faster-whisper), corre en tu máquina).
2. **Elige los mejores momentos** con Claude: busca fragmentos con buen gancho, que se entiendan solos y cierren bien, y les pone una **puntuación de viralidad** de 0 a 100 desglosada en *gancho*, *fluidez*, *valor* y *tendencia*. También escribe título, texto de gancho, descripción y hashtags.
3. **Ajusta los cortes** para no partir palabras y terminar la frase.
4. **Reencuadra a vertical** siguiendo la cara principal con movimiento de cámara suave y corte en seco en los cambios de plano. También ofrece el formato «video completo» con fondo difuminado.
5. **Agrega subtítulos animados** quemados en el video (estilo viral con la palabra activa resaltada, karaoke o clásico) y genera un `.srt` aparte.
6. **Normaliza el audio** a −14 LUFS, el volumen que usan TikTok, Reels y Shorts.

Incluye una **aplicación web** en español (subir video o pegar enlace, ver el progreso, reproducir y descargar los clips, copiar el texto para publicar y **editar** un clip: mover inicio y fin, cambiar el formato o el estilo de subtítulos y volver a generarlo) y una **línea de comandos**.

Sin clave de Claude funciona igual, pero elige los momentos con una heurística local (volumen, preguntas y palabras gancho), que es bastante menos precisa.

## Requisitos

- Python 3.10 o superior
- [FFmpeg](https://ffmpeg.org/) con `libass` (las versiones de apt, Homebrew y winget ya lo traen)
- Una clave de la API de Claude, en <https://console.anthropic.com> (opcional, pero muy recomendada)

## Instalación

```bash
cd clipper
python -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -e .
export ANTHROPIC_API_KEY="sk-ant-..."   # en Windows: set ANTHROPIC_API_KEY=sk-ant-...
```

La primera vez que transcribas, faster-whisper descarga el modelo de Hugging Face (`small` pesa unos 480 MB).

## Uso

### Aplicación web

```bash
clipper servidor
```

Abre <http://127.0.0.1:8000>. Para que otros equipos de tu red entren, usa `clipper servidor --host 0.0.0.0`.

### Terminal

```bash
# Archivo local: 5 clips de 20 a 60 s, copiados a ./clips
clipper procesar entrevista.mp4 --salida clips

# Enlace de YouTube, 8 clips cortos con subtítulos karaoke
clipper procesar "https://www.youtube.com/watch?v=..." --clips 8 --min 15 --max 30 --subtitulos karaoke

# Grabar 10 minutos de un canal en vivo (HLS) y sacar clips
clipper procesar "https://ejemplo.com/canal/en-vivo.m3u8" --indicaciones "las jugadas más emocionantes"

# Video completo con fondo difuminado en lugar de recorte
clipper procesar clase.mp4 --formato fit --subtitulos clasico
```

Cada clip queda como `NN-titulo.mp4` + `NN-titulo.srt`, y la terminal muestra la puntuación y por qué se eligió.

### Docker

```bash
cd clipper
docker build -t clipper-ia .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... -v clipper-datos:/data clipper-ia
```

El volumen `/data` guarda los proyectos y los modelos de Whisper para no volver a descargarlos.

## Configuración

Todo se ajusta con variables de entorno (ver `.env.example`; con Docker puedes usar `--env-file .env`):

| Variable | Por defecto | Para qué sirve |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Clave de Claude. Sin ella se usa la heurística local. |
| `CLIPPER_MODEL` | `claude-opus-5-5` | Modelo de Claude que elige los momentos. |
| `CLIPPER_EFFORT` | `high` | Cuánto razona Claude (`low` … `max`). Más alto es más fino, más lento y más caro. |
| `CLIPPER_SELECTOR` | `auto` | `heuristica` fuerza el modo sin IA. |
| `CLIPPER_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`. Los grandes transcriben mejor pero son más lentos. |
| `CLIPPER_WHISPER_DEVICE` | `auto` | `cuda` para usar la tarjeta gráfica NVIDIA. |
| `CLIPPER_DATA_DIR` | `./data` | Carpeta de proyectos, videos y clips. |
| `CLIPPER_MAX_CAPTURA` | `600` | Segundos máximos al grabar una transmisión en vivo. |
| `CLIPPER_FUENTE` | `Inter` | Fuente de los subtítulos (debe estar instalada). |

La llamada a Claude usa salida estructurada (JSON validado contra un esquema) y activa `fallbacks: "default"`: si el modelo rechaza la petición por sus filtros de seguridad, la API la reintenta sola en el modelo alternativo recomendado. Si Claude falla por cualquier motivo (sin red, sin saldo), el proyecto sigue con la heurística local y la web lo avisa.

**Costo aproximado** (estimación, no medición): una hora de video produce unos 15 000 tokens de transcripción. Con Claude Opus 5.5 (US$4 por millón de tokens de entrada y US$20 por millón de salida) eso da unos pocos centavos de entrada más el razonamiento y la respuesta; en total, del orden de US$0,10 a 0,50 por hora de video según `CLIPPER_EFFORT`. La transcripción y el render corren en tu equipo y no cuestan nada.

## Cómo está organizado

```
clipper/
├── clipper/
│   ├── pipeline.py     # orquesta: fuente → transcripción → selección → render
│   ├── media.py        # ffprobe/ffmpeg, descargas (yt-dlp y HLS), volumen del audio
│   ├── transcribe.py   # faster-whisper con marcas de tiempo por palabra
│   ├── highlights.py   # selección con Claude + heurística + ajuste de cortes
│   ├── reframe.py      # detección de caras, seguimiento y cortes de plano
│   ├── captions.py     # subtítulos ASS animados y SRT
│   ├── render.py       # comando ffmpeg final (1080×1920, H.264, AAC)
│   ├── jobs.py         # proyectos guardados en disco y cola de trabajo
│   ├── api.py          # API REST (FastAPI)
│   ├── cli.py          # comandos `procesar` y `servidor`
│   └── static/         # aplicación web (HTML, CSS y JS sin dependencias)
└── tests/              # pruebas con pytest (generan videos sintéticos con ffmpeg)
```

### API

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/api/jobs` | Crea un proyecto. Formulario con `file` o `url`, más `num_clips`, `min_duration`, `max_duration`, `layout` (`fill`/`fit`), `caption_style` (`viral`/`karaoke`/`clasico`/`ninguno`), `language`, `instructions`. |
| `GET` | `/api/jobs` | Lista de proyectos. |
| `GET` | `/api/jobs/{id}` | Estado, progreso y clips de un proyecto. |
| `POST` | `/api/jobs/{id}/clips/{n}/rerender` | Vuelve a generar un clip. JSON opcional: `start`, `end`, `layout`, `caption_style`. |
| `DELETE` | `/api/jobs/{id}` | Borra el proyecto y sus archivos. |
| `GET` | `/media/{id}/clips/{n}/clip.mp4` | Descarga el clip (también `clip.jpg` y `subs.srt`). |

## Pruebas

```bash
pip install -e ".[dev]"
pytest
```

Las pruebas no necesitan internet ni clave de Claude: generan videos sintéticos con ffmpeg, simulan Whisper y simulan la red al llamar al SDK de Anthropic.

## Límites actuales e ideas para seguir

- El seguimiento usa el detector de caras Haar de OpenCV: rápido y sin descargas, pero pierde caras muy de perfil. Un detector neuronal (YuNet o MediaPipe) y la detección de quién está hablando mejorarían los videos con varias personas.
- Falta el modo «pantalla dividida» para dos personas y la inserción automática de emojis o B-roll.
- La cola procesa un video a la vez; para muchos usuarios convendría un sistema de colas (Redis/Celery) y almacenamiento en la nube.
- No publica directamente en las redes; hay que descargar y subir los clips.

## Uso responsable

Procesa solo videos propios o para los que tengas permiso. Descargar o republicar contenido ajeno puede violar derechos de autor y los términos de servicio de cada plataforma.
