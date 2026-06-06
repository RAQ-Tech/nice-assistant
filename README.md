# Nice Assistant (Unraid-ready scaffold)

Containerized browser-first assistant with chat UI, persona/workspace memory tiers, visualizer, optional STT/TTS providers, and Ollama integration.

## Unraid quick setup

- **Container Name:** `nice-assistant`
- **Repository:** `ghcr.io/raq-tech/nice-assistant` (or `ghcr.io/raq-tech/nice-assistant:latest`)
- **Image port:** `3000`
- **Port mapping:** `3000:3000`
- **WebUI:** `http://[IP]:3000`
- **Icon file in repo:** `assets/nice-assistant-icon.svg`

### Recommended Unraid path mappings

- `/mnt/cache/appdata/nice-assistant` -> `/data` (required)
- `/mnt/user/Media/nice-assistant/archives` -> `/archives` (required archive target)

On container startup, the image automatically syncs project files into `/data/project` so the working source files live on your appdata share instead of only inside the image.

When this repository gets new commits on `main`, the GitHub Actions workflow publishes a refreshed `:latest` image to GHCR. In Unraid, using the repository above lets the **Update** button pull and redeploy that new image.

## Environment variables (defaults)

- `PORT=3000`
- `OLLAMA_BASE_URL=http://192.168.18.200:11434`
- `DATA_DIR=/data`
- `ARCHIVE_DIR=/archives`
- `AUDIO_HOT_LIMIT=200`
- `BACKUP_SNAPSHOT_LIMIT=10`
- `PROVIDER_TEST_TIMEOUT_SECONDS=10`
- `JOB_QUEUE_INTERACTIVE_WORKERS=1`
- `JOB_QUEUE_MEDIA_WORKERS=1`
- `PROJECT_ROOT=/data/project`
- `SYNC_PROJECT_ON_START=1` (set to `0` to keep local edits and skip overwrite from image)
- `AUTOMATIC1111_BASE_URL=http://127.0.0.1:7860` (default local image endpoint for `image_provider=local`)
- `COMFYUI_BASE_URL=http://127.0.0.1:8188` (default local image endpoint when local backend is ComfyUI)

## Features included in this scaffold

- Multi-user account creation + login (cookie session)
- First-login onboarding wizard (workspace + persona + default model + default memory mode)
- Chat list/new chat + transcript + per-chat model and memory mode
- Tiered memory CRUD APIs + basic UI controls:
  - global
  - workspace
  - persona
  - per-chat history
- Ollama model list endpoint + chat endpoint using selected model precedence
- Visualizer overlay toggle and ring-of-dots canvas driven by **actual `<audio>` playback analyser**
- Hold-to-talk recording (`MediaRecorder`) posting to `/api/stt`
- State indicator (`Listening`, `Thinking`, `Speaking`)
- STT/TTS provider settings in UI (disabled by default)
- OpenAI STT/TTS hooks (optional) + local provider placeholders
- Provider readiness checks in Settings for Ollama, OpenAI, Kokoro, Automatic1111, and ComfyUI
- Local image generation through Automatic1111 (`/sdapi/v1/txt2img`) or ComfyUI (`/prompt`, `/history/{prompt_id}`, `/view`) with per-user endpoint override in Settings
- ffmpeg included for audio conversion
- Audio hot-cache rotation into archives (move, do not delete)
- DB backup + log archival foundation
- Isolated async queue lanes so slow media/provider jobs do not block normal chat work
- Admin-only backup center for restorable ZIP snapshots
- `/api/tts/stream` placeholder for future streaming migration

## Build/run

```bash
docker build -t nice-assistant .
docker run --name nice-assistant -p 3000:3000 \
  -e OLLAMA_BASE_URL=http://192.168.18.200:11434 \
  -v /mnt/cache/appdata/nice-assistant:/data \
  -v /mnt/user/Media/nice-assistant/archives:/archives \
  nice-assistant
```

## Developer verification

This project is served by the Python backend in `app/server.py`; Docker is the deployment runtime. The `package.json` scripts are convenience wrappers for Python commands, not a Node.js app.

Run the full unit/API suite:

```bash
python -m unittest discover -s tests -v
```

Run the process-level smoke check:

```bash
python scripts/smoke_check.py
```

Equivalent npm convenience wrappers are also available where `python` is on PATH:

```bash
npm test
npm run smoke
npm start
```

## Admin backups and manual restore

Admins can create backup snapshots from Settings -> Data. Snapshots are stored under `/archives/backups` as ZIP files named like `nice-assistant-snapshot-YYYYMMDD_HHMMSS-token.zip`.

Every snapshot includes `manifest.json` and a consistent SQLite backup named `nice_assistant.db`, created with SQLite's online backup API. `settings.json` is included only when present. Logs are excluded; use the admin diagnostic log download when you need redacted logs.

`Create backup` stores database/settings metadata only. `Create full backup` also includes regular files from `/data/audio`, `/data/images`, `/data/videos`, and `/data/stt_recordings`. Symlinks are skipped.

To restore manually:

1. Stop the container.
2. Extract the snapshot ZIP somewhere safe.
3. Copy `nice_assistant.db` back to `/data/nice_assistant.db`.
4. If the snapshot includes media directories, copy those files back under the matching `/data` directories.
5. Restart the container.

Backup ZIPs can contain stored API keys and password hashes. Treat them as sensitive admin artifacts.

## Mobile microphone note

For iPhone/Android browsers, microphone capture usually requires **HTTPS** (or localhost secure contexts). LAN HTTP works for desktop testing first.

## API highlights

- `GET /health`
- `GET /api/models`
- `POST /api/providers/test`
- `POST /api/users`, `POST /api/login`
- `GET/POST /api/admin/backups`, `GET /api/admin/backups/:name/download`, `DELETE /api/admin/backups/:name`
- `POST/GET /api/workspaces`, `POST/GET /api/personas`
- `POST /api/chat`
- `POST /api/stt` (multipart `file`)
- `POST /api/tts`, `GET /api/tts/audio/:id`
- `POST/GET /api/chats`, `GET/PUT /api/chats/:id`
- `GET/POST/DELETE /api/memory/global`
- `GET/POST /api/memory/workspace/:id`
- `GET/POST /api/memory/persona/:id`
- `POST /api/tts/stream` (stub)
