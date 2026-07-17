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

ENV NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC=0

WORKDIR /opt/nice-assistant
COPY . /opt/nice-assistant
COPY --from=browser-build /src/web /opt/nice-assistant/web
COPY entrypoint.sh /entrypoint.sh
RUN test ! -e .local \
    && test ! -e .env \
    && test ! -e build \
    && test ! -e dist \
    && test ! -e .coverage \
    && test ! -e .pytest_cache \
    && test ! -e .ruff_cache \
    && test ! -e .mypy_cache \
    && test ! -e htmlcov \
    && test ! -e tests \
    && test ! -e frontend/tests \
    && test ! -e frontend/e2e \
    && ! find . -type d -name '__pycache__' -print -quit | grep -q . \
    && ! find . -type f \( -name '*.pyc' -o -name '*.pyo' \) \
      -print -quit | grep -q . \
    && ! find . -maxdepth 1 -name '*.egg-info' -print -quit | grep -q . \
    && ! find . -maxdepth 1 -type f -name '.env*' ! -name '.env.example' \
      -print -quit | grep -q . \
    && ! find . -type f \
      \( -name 'nice_assistant_deploy_ed25519*' -o -name 'remote.json' \) \
      -print -quit | grep -q . \
    && chmod 0700 scripts/deployment/nice_assistant_deploy_guard.sh \
    && chmod 0600 scripts/deployment/create_container_payload.jq \
      scripts/deployment/normalize_container_config.jq \
      scripts/deployment/guard_bundle_manifest.json \
    && pip install --no-cache-dir . \
    && rm -rf build dist ./*.egg-info \
    && sed -i 's/\r$//' /entrypoint.sh \
    && chmod +x /entrypoint.sh

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; url='http://127.0.0.1:%s/health' % os.environ.get('PORT','3000'); sys.exit(0 if urllib.request.urlopen(url, timeout=3).status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
