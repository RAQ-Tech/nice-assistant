#!/usr/bin/env bash
set -euo pipefail

DEVELOPMENT_PROJECT_SYNC="${NICE_ASSISTANT_DEVELOPMENT_PROJECT_SYNC:-0}"

if [[ "${DEVELOPMENT_PROJECT_SYNC}" == "1" ]]; then
  PROJECT_ROOT="${PROJECT_ROOT:-/data/project}"
  SYNC_PROJECT_ON_START="${SYNC_PROJECT_ON_START:-1}"
  mkdir -p "${PROJECT_ROOT}"
  if [[ "${SYNC_PROJECT_ON_START}" == "1" ]]; then
    echo "[nice-assistant] Development sync from image to ${PROJECT_ROOT}"
    cp -a /opt/nice-assistant/. "${PROJECT_ROOT}/"
  else
    echo "[nice-assistant] Development sync disabled; using existing ${PROJECT_ROOT}"
  fi
  cd "${PROJECT_ROOT}"
else
  echo "[nice-assistant] Using immutable application code from /opt/nice-assistant"
  cd /opt/nice-assistant
fi

exec python -u -m app.asgi
