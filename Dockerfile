FROM node:24.15-alpine AS browser-build

WORKDIR /src
COPY package.json package-lock.json tsconfig.json vite.config.ts ./
COPY frontend ./frontend
RUN npm ci && npm run frontend:build

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

ENV PORT=3000 \
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    DATA_DIR=/data \
    ARCHIVE_DIR=/archives \
    AUDIO_HOT_LIMIT=200

ENV PROJECT_ROOT=/data/project \
    SYNC_PROJECT_ON_START=1

WORKDIR /opt/nice-assistant
COPY . /opt/nice-assistant
COPY --from=browser-build /src/web /opt/nice-assistant/web
COPY entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir . && sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; url='http://127.0.0.1:%s/health' % os.environ.get('PORT','3000'); sys.exit(0 if urllib.request.urlopen(url, timeout=3).status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
