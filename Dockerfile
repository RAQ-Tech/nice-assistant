FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

ENV PORT=3000 \
    OLLAMA_BASE_URL=http://192.168.18.200:11434 \
    DATA_DIR=/data \
    ARCHIVE_DIR=/archives \
    AUDIO_HOT_LIMIT=200

WORKDIR /app
COPY app /app/app
COPY web /app/web
COPY assets /app/assets

EXPOSE 3000
CMD ["python", "-u", "app/server.py"]
