# Nice Assistant

Private-LAN assistant foundation with a FastAPI service layer, typed modular browser,
durable conversation
turns and jobs, causally bounded Ollama chat, durable conversation summaries,
review-first Memory v2, owner-scoped artifacts, permissioned media
capabilities, separately configured platform Task Models, an operator-managed
media resource catalog with deterministic execution plans, OpenAI STT, and
consent-bound persona visual identity with a stateless CompreFace LAN adapter,
truthful optional GPU capacity coordination across local providers,
reviewed-reference ComfyUI persona conditioning with durable provenance, and
completed-file OpenAI/Kokoro-compatible TTS. The
product direction is voice-first; realtime speech and natural turn-taking are
deliberately still later roadmap work.

## Unraid quick setup

- **Container Name:** `nice-assistant`
- **Repository:** `ghcr.io/raq-tech/nice-assistant` (or `ghcr.io/raq-tech/nice-assistant:latest`)
- **Image port:** `3000`
- **Port mapping:** `3000:3000`
- **WebUI:** `http://[IP]:3000`
- **Icon file in repo:** `assets/nice-assistant-icon.svg`

### Required path mappings

- `<persistent-data-path>` -> `/data`
- `<archive-storage-path>` -> `/archives`

On container startup, the image automatically syncs project files into `/data/project` so the working source files live on your appdata share instead of only inside the image.

When this repository gets new commits on `main`, the GitHub Actions workflow publishes a refreshed `:latest` image to GHCR. In Unraid, using the repository above lets the **Update** button pull and redeploy that new image.

The public acceptance checklist and deliberately unaccepted capabilities are in
[`docs/deployment-acceptance.md`](docs/deployment-acceptance.md). Keep exact
deployment evidence under the ignored `.local/` directory.

## Environment variables (defaults)

- `PORT=3000`
- `OLLAMA_BASE_URL=http://127.0.0.1:11434` (override with the Ollama LAN endpoint)
- `DATA_DIR=/data`
- `ARCHIVE_DIR=/archives`
- `AUDIO_HOT_LIMIT=200`
- `BACKUP_SNAPSHOT_LIMIT=10`
- `PROVIDER_TEST_TIMEOUT_SECONDS=10`
- `NICE_ASSISTANT_MASTER_KEY` (required before saving provider secrets; preserve it across redeployments)
- `JOB_QUEUE_INTERACTIVE_WORKERS=1`
- `JOB_QUEUE_MEDIA_WORKERS=1`
- `DEFAULT_CONTEXT_WINDOW_TOKENS=4096`
- `CONTEXT_SUMMARY_TRIGGER_RATIO=0.75`
- `CONTEXT_MAX_COMPACTION_PASSES=2`
- `MEMORY_CANDIDATE_LIMIT=5`
- `PROJECT_ROOT=/data/project`
- `SYNC_PROJECT_ON_START=1` (set to `0` to keep local edits and skip overwrite from image)
- `AUTOMATIC1111_BASE_URL=http://127.0.0.1:7860` (default local image endpoint for `image_provider=local`)
- `COMFYUI_BASE_URL=http://127.0.0.1:8188` (default local image endpoint when local backend is ComfyUI)
- `NICE_ASSISTANT_ALLOWED_ORIGINS` (comma-separated exact HTTPS reverse-proxy origins)
- `NICE_ASSISTANT_SECURE_COOKIES=0` (set to `1` behind HTTPS)
- `NICE_ASSISTANT_TRUST_PROXY_HEADERS=0` (enable only behind a trusted header-sanitizing proxy)
- `NICE_ASSISTANT_PROVIDER_HOST_ALLOWLIST` (comma-separated exceptional trusted provider hostnames)
- `LOGIN_MAX_ATTEMPTS=5`, `LOGIN_WINDOW_SECONDS=300`, `LOGIN_LOCKOUT_SECONDS=900`
- `MINIMUM_FREE_STORAGE_BYTES=134217728`
- `AUDIO_ARCHIVE_RETENTION_DAYS=30`, `STT_RECORDING_RETENTION_DAYS=30`, `LOG_ARCHIVE_RETENTION_DAYS=30`
- `DAILY_DATABASE_BACKUP_LIMIT=14`

Generate a deployment master key once, store it as an Unraid secret/environment
variable, and retain it with the deployment recovery material:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Existing plaintext OpenAI keys are migrated to encrypted storage automatically.
Startup is refused while any provider secret exists without this key, and a new
provider secret is never written in plaintext.

## Current runtime and features

The public runtime is FastAPI/Uvicorn (`python -m app.asgi`). The TypeScript/Vite
browser and all public application routes use `/api/v1`, with interactive
documentation at `/api/v1/docs`. The legacy `/api` browser surface, raw HTTP
server, proxy, bridge, and second listener have been removed.

- Multi-user account creation + login (cookie session)
- Same-origin write-header/origin enforcement, bounded login lockout, strict
  cookies, optional HTTPS `Secure` cookies, and private-LAN provider URL policy
- First-login onboarding wizard (workspace + persona + default model + default memory mode)
- Chat list/new chat + transcript + per-chat model and memory mode, with
  explicit individual and bulk hide/delete actions
- Tiered memory CRUD APIs + basic UI controls:
  - global
  - workspace
  - persona
  - per-chat history
- Ollama model listing plus streamed NDJSON chat behind a provider-neutral contract
- Durable conversation turns linked one-to-one with jobs; restart recovery marks
  unfinished work failed with `interrupted by server restart`
- Same-chat turns execute causally, independent chats may run concurrently, and
  prompts use explicit budgets with durable incremental summaries
- `off`/`saved` memory modes with post-turn pending candidates, explicit approval,
  provenance, revision history, reversible forget/undo, permanent individual or
  bulk delete, and active-only scoped FTS retrieval
- Per-model context allocation is sent to Ollama as `num_ctx` and recorded with
  estimated/actual prompt usage on the turn
- Authenticated turn events over SSE with snapshots and bounded in-process replay
- Cooperative queued/running cancellation with an active composer Cancel
  control; acknowledged media cancellation returns to Ready, and late provider
  results are discarded
- Typed platform capability planning creates durable approval cards instead of
  triggering work from persona output, keywords, or hidden response tags
- Owner-scoped capability approval, denial, cancellation, idempotent explicit
  actions, audit history, and protected results
- Separate Task Model roles for titles, summaries, reviewable memory extraction,
  and semantic capability planning, with per-user models, budgets, readiness,
  fallback, and content-free run audits in Settings
- Persona models are not offered platform tools and cannot select media
  providers, checkpoints, workflows, LoRAs, or identity controls
- Operator-managed model/LoRA/workflow metadata and explicit compatibility feed
  a deterministic coordinator; model-requested approval cards show the selected
  resources, explanation, estimates, warnings, or blocked reason before execution
- Catalog planning never infers fitness from filenames or claims live GPU
  residency; ComfyUI editing requires exact declared source/mask bindings and
  Automatic1111 remains generation-only
- Admin-only GPU coordination is disabled by default; observe mode gates
  catalog-planned local image jobs on real provider telemetry, while managed
  release requires endpoint-bound exclusive-control authorization and fresh
  capacity verification and reclaims the authorized media provider after local
  image work before the next shared-resource job starts
- Consent-bound persona visual identity profiles with normalized protected
  references, explicit review/deletion, encrypted verifier credentials, durable
  comparisons, and truthful verified/rejected/unverified media status
- Identity-aware persona requests bind reviewed references through exact ComfyUI
  inputs, run post-generation comparison, durably record attempts, and apply a
  bounded correction/rerun policy before returning verified or explicit
  unverified output
- Visualizer overlay toggle and ring-of-dots canvas driven by **actual `<audio>` playback analyser**
- Hold-to-talk recording (`MediaRecorder`) posting to `/api/v1/speech/transcriptions`
- State indicator (`Listening`, `Thinking`, `Speaking`)
- STT/TTS provider settings in UI (disabled by default)
- OpenAI STT plus OpenAI and Kokoro-compatible request/response TTS
- Provider readiness checks in Settings for Ollama, OpenAI, Kokoro, Automatic1111, and ComfyUI
- Local image generation through Automatic1111 (`/sdapi/v1/txt2img`) or ComfyUI (`/upload/image`, `/prompt`, `/history/{prompt_id}`, `/view`) with per-user endpoint override in Settings
- Enabling a media provider after initial setup bootstraps a missing starter catalog model without replacing operator-managed catalog resources
- Persona-chat image planning keeps the user's requested subject authoritative: unrelated images use ordinary catalog models, while persona images require an explicitly configured identity workflow and show actionable block reasons when it is missing
- ffmpeg included for audio conversion
- Audio hot-cache rotation into archives with durable replay-path updates and
  configurable age retention
- Redacted JSON correlation logs; admin readiness, request/provider/job/queue,
  storage, and retention reporting
- Atomic artifact persistence with safe full-disk/empty-provider failures
- Verified DB backups plus non-mutating archive/integrity/migration restore drills
- Isolated async queue lanes so slow media/provider jobs do not block normal chat work
- Admin-only backup center for restorable ZIP snapshots
- Realtime/streaming TTS has no advertised endpoint because it is not implemented yet
- Local STT is not implemented and cannot be selected in the UI

## Build/run

```bash
docker build -t nice-assistant .
docker run --name nice-assistant -p 3000:3000 \
  -e OLLAMA_BASE_URL=http://<OLLAMA_LAN_HOST>:11434 \
  -v <PERSISTENT_DATA_PATH>:/data \
  -v <ARCHIVE_STORAGE_PATH>:/archives \
  nice-assistant
```

## Developer verification

This project is served by `app.asgi`; Docker is the deployment runtime. Browser
source lives in `frontend/src` and is built by Vite into packageable files under
`web`. Node.js 24 and Python 3.11+ are required for development verification.

Install browser and Python verification dependencies:

```bash
npm ci
npx playwright install chromium
python -m pip install -e '.[dev]'
```

Run focused browser checks:

```bash
npm run frontend:typecheck
npm run frontend:test
npm run frontend:build
npm run frontend:e2e
```

Screen a candidate local Ollama Task Model with the developer-only cases (output
content is hidden unless `--show-output` is explicitly added):

```bash
python scripts/evaluate_task_models.py --base-url http://OLLAMA_HOST:11434 --model MODEL_NAME
```

Run the full Python unit/API suite:

```bash
python -m unittest discover -s tests -v
```

Run the process-level smoke check:

```bash
python scripts/smoke_check.py
```

Non-browser API clients must include `X-Nice-Assistant-CSRF: 1` on every
state-changing `/api/v1` request.

After building the image, run the installed-package container smoke on Windows:

```powershell
docker build -t nice-assistant:local .
pwsh -File scripts/container_smoke_check.ps1 -Image nice-assistant:local
```

Run the complete repository check (typecheck, browser unit/build, Python static
analysis and coverage, process smoke, and Playwright journeys):

```bash
python scripts/verify.py
```

For a foundation change, require three consecutive suite passes:

```bash
python scripts/verify.py --repeat 3
```

Public commits are also checked with `python scripts/audit_public_repo.py`.
Installation-specific records and the optional private-value watchlist belong in
the ignored `.local/` directory, never in tracked documentation.

On Windows, use `py -3` in place of `python` when the Windows Store alias is not
the installed interpreter.

Equivalent npm wrappers are available where `python` is on PATH:

```bash
npm test
npm run smoke
npm run verify
npm start
```

## Admin backups and manual restore

Admins can create backup snapshots from Settings -> Data. Snapshots are stored under `/archives/backups` as ZIP files named like `nice-assistant-snapshot-YYYYMMDD_HHMMSS-token.zip`.

Use the snapshot's **Verify** action before relying on it. Verification extracts
only a temporary database copy, runs SQLite integrity plus current migrations,
and reports the result without changing live data.

Every snapshot includes `manifest.json` and a consistent SQLite backup named `nice_assistant.db`, created with SQLite's online backup API. `settings.json` is included only when present. Logs are excluded; use the admin diagnostic log download when you need redacted logs.

`Create backup` stores database/settings metadata only. `Create full backup` also includes regular files from `/data/audio`, `/data/images`, `/data/videos`, `/data/stt_recordings`, and `/data/identity_references`. Symlinks are skipped.

To restore manually:

1. Stop the container.
2. Extract the snapshot ZIP somewhere safe.
3. Copy `nice_assistant.db` back to `/data/nice_assistant.db`.
4. If the snapshot includes media directories, copy those files back under the matching `/data` directories.
5. Restart the container.

Backup ZIPs can contain stored API keys and password hashes. Treat them as sensitive admin artifacts.

Validate a snapshot without touching live data:

```bash
python scripts/backup_restore_drill.py /path/to/nice-assistant-snapshot-....zip
```

## Mobile microphone note

For iPhone/Android browsers, microphone capture usually requires **HTTPS** (or localhost secure contexts). LAN HTTP works for desktop testing first.

## API highlights

- `GET /health`
- `GET /ready`
- `GET/POST /api/v1/chats`, `GET/PUT/DELETE /api/v1/chats/:id`
- `POST /api/v1/chats/:id/turns`, `GET /api/v1/turns/:id`
- `GET /api/v1/turns/:id/events` (authenticated SSE)
- `GET/DELETE /api/v1/jobs/:id`
- `GET /api/v1/capabilities`, `GET /api/v1/capability-requests`
- `GET/DELETE /api/v1/capability-requests/:id`
- `POST /api/v1/capability-requests/:id/approval`, `POST /api/v1/capability-requests/:id/denial`
- `GET /api/v1/capability-requests/:id/events`
- `GET /api/v1/models`, `POST /api/v1/provider-checks`
- `GET /api/v1/task-models`, `PUT /api/v1/task-models/:role`
- `POST /api/v1/task-models/:role/check`, `GET /api/v1/task-model-runs`
- `POST /api/v1/media/image-jobs`, `POST /api/v1/media/image-edit-jobs`, `POST /api/v1/media/video-jobs`
- `GET /api/v1/media-catalog`, `PUT /api/v1/media-catalog/settings`
- `POST /api/v1/media-catalog/resources`, `GET/PUT/DELETE /api/v1/media-catalog/resources/:id`
- `POST /api/v1/media-catalog/plan-previews`, `GET /api/v1/media-plans/:id`, `GET /api/v1/media-plans/:id/attempts`
- `GET/PUT /api/v1/identity-validation/settings`, `POST /api/v1/identity-validation/check`
- `GET/PUT /api/v1/personas/:id/visual-identity`, consent, reference review, validation, and history routes
- `GET /api/v1/media/:id/identity-status`
- `GET /api/v1/admin/observability`, `POST /api/v1/admin/backups/:name/verify`
- `GET/PUT /api/v1/admin/resource-coordination`, `POST /api/v1/admin/resource-coordination/check`
- `GET /api/v1/admin/resource-coordination/events`
- `POST /api/v1/speech/syntheses`, `POST /api/v1/speech/transcriptions`
- `GET /api/v1/audio/:id`, `GET /api/v1/media?kind=image`, `GET /api/v1/media/:id`
- Typed settings, workspace, persona, memory, backup, and diagnostic routes under `/api/v1`

SSE disconnect does not cancel a turn. Turn and direct-job cancellation uses an
explicit `DELETE /api/v1/jobs/:id`; capability cards cancel through
`DELETE /api/v1/capability-requests/:id`.
Model-requested media requires an explicit capability approval; direct media
buttons are already explicit user actions and use the same audited job path.
Replay via `Last-Event-ID` is bounded and survives only while the process and
short retention window remain alive; final turn/job state is durable in SQLite.
