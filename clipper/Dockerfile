FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY clipper ./clipper
RUN pip install --no-cache-dir .

ENV CLIPPER_DATA_DIR=/data \
    HF_HOME=/data/modelos
VOLUME /data
EXPOSE 8000

CMD ["clipper", "servidor", "--host", "0.0.0.0", "--puerto", "8000"]
