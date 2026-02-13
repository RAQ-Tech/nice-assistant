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
- `PROJECT_ROOT=/data/project`
- `SYNC_PROJECT_ON_START=1` (set to `0` to keep local edits and skip overwrite from image)

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
- ffmpeg included for audio conversion
- Audio hot-cache rotation into archives (move, do not delete)
- DB backup + log archival foundation
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

## Mobile microphone note

For iPhone/Android browsers, microphone capture usually requires **HTTPS** (or localhost secure contexts). LAN HTTP works for desktop testing first.

## API highlights

- `GET /health`
- `GET /api/models`
- `POST /api/users`, `POST /api/login`
- `POST/GET /api/workspaces`, `POST/GET /api/personas`
- `POST /api/chat`
- `POST /api/stt` (multipart `file`)
- `POST /api/tts`, `GET /api/tts/audio/:id`
- `POST/GET /api/chats`, `GET/PUT /api/chats/:id`
- `GET/POST/DELETE /api/memory/global`
- `GET/POST /api/memory/workspace/:id`
- `GET/POST /api/memory/persona/:id`
- `POST /api/tts/stream` (stub)
