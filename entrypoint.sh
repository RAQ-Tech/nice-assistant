#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/data/project}"
SYNC_PROJECT_ON_START="${SYNC_PROJECT_ON_START:-1}"

mkdir -p "${PROJECT_ROOT}"

if [[ "${SYNC_PROJECT_ON_START}" == "1" ]]; then
  echo "[nice-assistant] Syncing project files from image to ${PROJECT_ROOT}"
  cp -a /opt/nice-assistant/. "${PROJECT_ROOT}/"
else
  echo "[nice-assistant] Skipping project sync (SYNC_PROJECT_ON_START=${SYNC_PROJECT_ON_START})"
fi

cd "${PROJECT_ROOT}"
exec python -u app/server.py
