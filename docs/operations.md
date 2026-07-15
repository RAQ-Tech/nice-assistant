# Operations

## Deployment topology

Nice Assistant runs as a lean application container with persistent `/data` and
archive storage. Local TTS, STT, LLM, image, and video engines run as separate
LAN services with their own health checks and hardware configuration.

Browser microphone use, particularly on mobile, requires an HTTPS origin. Place
the application behind a LAN HTTPS reverse proxy or another trusted secure
access layer.

## Operational requirements

- Health distinguishes application liveness from provider readiness.
- Structured logs use request and turn IDs and redact credentials.
- Track provider latency/failure/fallback, queue depth, storage retention, and
  realtime disconnects. Track estimated versus actual prompt tokens, compaction
  frequency, and degraded-context reasons.
- Graceful shutdown stops accepting work, cancels or marks unfinished jobs, and
  closes realtime sessions before process exit.
- Backup documentation includes creation, retention, restoration, validation,
  and rollback after a failed schema migration.

Hardware-specific commands, measured latency, and service URLs belong in the
deployment record created during real-environment acceptance, not in product
defaults.

`GET /health` is liveness. `GET /ready` checks SQLite access, required storage
directories, and `MINIMUM_FREE_STORAGE_BYTES` (128 MiB by default) and returns
`503` when the deployment should not receive work. Admins can inspect bounded,
content-free request/provider/job latency and outcomes, queue depth, storage,
retention, and readiness at `GET /api/v1/admin/observability`.

All non-browser API clients must send `X-Nice-Assistant-CSRF: 1` on `POST`,
`PUT`, `PATCH`, and `DELETE`. For HTTPS reverse proxy deployments set
`NICE_ASSISTANT_ALLOWED_ORIGINS` to the comma-separated exact browser origins,
set `NICE_ASSISTANT_SECURE_COOKIES=1`, and set
`NICE_ASSISTANT_TRUST_PROXY_HEADERS=1` only when client forwarding headers are
overwritten by a trusted proxy. That flag enables Uvicorn proxy-header parsing;
restrict `FORWARDED_ALLOW_IPS` to the proxy address rather than using a broad
wildcard. Direct LAN HTTP must leave secure cookies off.

Typed desktop chat also supports direct LAN HTTP: transient browser message IDs
fall back to `crypto.getRandomValues` because `crypto.randomUUID` is restricted
to secure contexts. This does not make microphone capture available; mobile and
voice deployments still require HTTPS.

Provider hosts outside private/Tailscale literals and recognized `.lan`,
`.local`, localhost, or container service names must be explicitly listed in
`NICE_ASSISTANT_PROVIDER_HOST_ALLOWLIST`. Do not add a public hostname merely to
silence validation; an entry authorizes server-side requests to that host.

## Runtime lifecycle

Start development and installed deployments with `python -m app.asgi`. Uvicorn is
the only listener. Application lifespan starts database migrations/recovery and
the separate interactive/media queue lanes, then cancels live provider tokens,
joins workers, expires event subscribers, and disposes the SQLAlchemy engine on
shutdown.

For browser development, run `npm run backend:dev` and `npm run dev` in separate
terminals. Production images build `frontend/src` in a pinned Node stage and copy
only generated `web` assets into the Python runtime. Never edit `web/app.js` or
`web/styles.css` directly; regenerate them with `npm run frontend:build` and
commit source and generated output together.

The Step 9 cutover removes `/api` compatibility routes. Before deployment,
migration `0007_browser_v1_cutover` changes stored media links to protected
media IDs. Roll back by restoring the pre-migration database and prior image as
described below; do not partially restore only the static browser assets.

After an unclean stop, startup changes every queued/running job, turn,
capability request, and Task Model run to
`failed` with `interrupted by server restart`. User messages remain durable;
provider failures and interrupted work never create assistant messages. Operators
should inspect final job/turn state rather than expect SSE replay after a restart.

SSE replay is an availability convenience, not durable event storage. It is
bounded by event count, bytes, and a short terminal retention window. Disconnecting
a client does not stop generation; use the authenticated job DELETE endpoint.
Provider cancellation is cooperative, so a provider incapable of interruption
may consume resources until its request returns even though the late result is
discarded.

Model-requested capabilities remain pending until an authenticated user approves
or denies them. Direct media buttons create explicit capability records and may
start immediately. `Idempotency-Key` is supported on media-job creation; reuse
with a different payload returns a conflict. Operators can inspect durable
capability history through `/api/v1/capability-requests/{id}/events`. A configured
provider is not proof of healthy external capacity; readiness checks and final
job state remain authoritative.

Task Model profiles live under Settings -> Task Models. A blank model means the
first installed Ollama model, which is reported by the readiness check; explicit
model names are safer for repeatable deployments. The default single interactive
worker serializes chat, summary, title, memory extraction, and capability
planning. Raising `JOB_QUEUE_INTERACTIVE_WORKERS` permits concurrency and may
cause shared-VRAM contention. A fallback model may also incur Ollama load/swap
latency.

For developer qualification on the real LAN service, run:

```bash
python scripts/evaluate_task_models.py --base-url http://OLLAMA_HOST:11434 --model MODEL_NAME
```

The command emits pass/fail, latency, and safe failure details. It does not emit
generated task content unless `--show-output` is deliberately supplied. It is a
developer/operator check, not an end-user product screen.

Media resources live under Settings -> Media Catalog. Operators supply exact
external IDs, controlled strengths, compatibility, priority, defaults, and
estimated VRAM/load cost. The planning budget is not live GPU telemetry and a
zero estimate is unknown. Keep resources disabled until their exact external ID
and adapter path have been verified. Editing a selected resource revision makes
an already-presented capability plan stale; deny/recreate the request rather
than expecting approval to choose a substitute. Direct media buttons retain the
legacy provider setting and are recorded as manual catalog bypasses.

GPU coordination lives under Settings -> GPU Coordination and is disabled by
default. `observe` polls the configured ComfyUI or Automatic1111 endpoint and
admits catalog-planned local image jobs only when reported free VRAM covers the
plan estimate plus the configured reserve. Waiting jobs remain queued without
occupying the media worker. Chat, Task Model, and admitted local-image work use
one process-local lease while coordination is enabled, with queued interactive
work taking priority.

`managed` may request coarse model release from the target media service and
Ollama, but only after an administrator checks both exclusive control and
release for that exact endpoint fingerprint. Use that authorization only when
Nice Assistant is the sole client allowed to submit work to the service.
Changing a provider URL invalidates authorization. A provider adapter's release
support is not proof that the deployed version accepts the call; Nice Assistant
records failures and always remeasures capacity before admission. Managed
release can add model reload latency to later chat or media requests. After a
local image job that actually started, managed mode also reclaims that exact
authorized media provider while retaining the process lease; a queued chat
cannot begin until the release attempt finishes. It does not unload Ollama in
this post-job phase.

Unknown telemetry, zero catalog estimates, direct manual media actions, video,
and external clients are not silently guessed. Unknown-demand local image jobs
skip measured-capacity admission but still participate in serialization and, in
managed mode, authorized post-job reclamation. A queued job cancelled before it
starts never causes a release; cancellation after execution starts retains the
record and performs cleanup after cooperative provider termination. Catalog
work with unavailable telemetry times out safely according to the configured
maximum. Inspect the content-free coordination event list for waiting, release,
admission, timeout, and cancellation outcomes; release details distinguish
pre-admission from post-job controls. Provider URLs, credentials, prompts, and
generated content are not included in those events.

### Local deployment evidence

Keep exact provider endpoints, hostnames, hardware, capacity, latency, and
restart measurements in `.local/deployment-acceptance.md`. That directory is
ignored by Git. The public `docs/deployment-acceptance.md` file is the reusable
checklist and current product boundary, not one operator's infrastructure log.

The accepted coordination shape authorizes release only for an endpoint the
operator has confirmed is exclusive to Nice Assistant. Shared providers remain
unauthorized. Measure post-job cleanup and restart recovery on the actual target
hardware, but publish only generalized product outcomes unless a deployment
owner deliberately approves the underlying details for release.

Visual identity lives under Settings -> Visual Identity. Deploy CompreFace as a
separate LAN service, create a verification API key there, then store its base
URL and key in Nice Assistant. The adapter uses stateless two-image verification;
do not create duplicate persona subjects for this integration. Keep the provider
disabled until its readiness check succeeds. The default comparison threshold is
an operator starting point, not a universal identity guarantee, and should be
reviewed against representative persona references.

Identity reference uploads are normalized into `/data/identity_references`.
Withdrawing consent removes those files and cancels active comparison jobs.
Queued/running identity validations also become safe `interrupted` errors after
an unclean restart. Full backups include the identity-reference directory;
metadata-only backups include profiles/audit rows but not the images.

Identity-aware generation is configured in Settings -> Media Catalog, not in an
end-user test lab. Add and verify a ComfyUI API-format workflow containing the
actual identity extension nodes, declare the `identity_control` feature, and set
`default_settings.identity_image_bindings` to exact node/input pairs already in
the inline `workflow_patch`. Example:

```json
{
  "workflow_patch": {
    "100": {"class_type": "LoadImage", "inputs": {"image": "placeholder.jpg"}},
    "101": {"class_type": "IPAdapterAdvanced", "inputs": {"image": ["100", 0]}}
  },
  "identity_image_bindings": [{"node_id": "100", "input_name": "image"}]
}
```

The example node is illustrative; the operator must export and test the graph
against the deployed custom nodes. Nice Assistant will not infer missing inputs.
Plan previews without a persona are blocked for execution by design. Test through
a persona chat with active consent and an approved reference. The resulting
artifact is compared after generation. Below-threshold results may rerun the
generation graph or use a compatible `image_to_image` identity graph that
declares both `identity_image_bindings` and `source_image_bindings`. Explicit
edit graphs require `source_image_bindings`; inpaint/outpaint graphs also require
`mask_image_bindings`. These exact node/input pairs are never inferred. Cancellation
checks each stage and closes active responses where possible, but ComfyUI retains
control of uploaded-input cleanup and an in-flight request may last until timeout.

Context defaults are `DEFAULT_CONTEXT_WINDOW_TOKENS=4096`,
`CONTEXT_SUMMARY_TRIGGER_RATIO=0.75`, and
`CONTEXT_MAX_COMPACTION_PASSES=2`. Per-model UI settings override the window and
are sent to Ollama as `num_ctx`; larger allocations increase provider memory use.

`MEMORY_CANDIDATE_LIMIT=5` limits post-turn suggestions to one through ten.
Extraction jobs are durable and lower-priority than queued chat turns. A failed
extraction does not fail the source turn. Monitor failed `memory_extraction` jobs,
pending-review depth, and FTS integrity. Backups contain memory content, rejected
and forgotten history, and source provenance and remain sensitive.

## Backup and migration recovery

Database backups use SQLite's online backup API and are integrity-checked before
their temporary file replaces the published backup. This includes committed data
still present in the WAL. Backups are sensitive because they contain application
state and may contain encrypted provider credentials.

Migration `0008_capability_framework` reconstructs the SQLite jobs table to add
its capability foreign key. Migration `0009_task_models` adds profiles and run
audits without reconstructing conversation/media tables and seeds existing users
from their global model. Migration `0010_media_catalog` adds catalog settings,
resources, compatibility, and immutable plans, then imports enabled legacy image
and video settings. Migration `0011_persona_identity` adds identity tables
without reconstructing existing persona/media/job tables. Migration
`0012_resource_coordination` adds policy, endpoint-fingerprint authorization,
and resource audit tables without reconstructing existing job/media tables.
Migration `0013_identity_generation` adds identity snapshots to media plans and
reconstructs `media_files` only to add the nullable plan foreign key and index;
the migration test proves existing artifacts and plans survive.
Migration `0014_media_correction_workflows` adds the durable attempt ledger and
does not reconstruct existing tables. Startup marks unfinished attempts as safe
interrupted errors.
Schema migrations are
forward-only. Rollback means
stopping the service,
restoring the verified pre-migration backup, restoring the previous application
image, and then restarting. Do not use `alembic downgrade` as a production
recovery mechanism.

Before relying on a snapshot, an admin can call
`POST /api/v1/admin/backups/{name}/verify`, or run the offline, non-mutating
drill:

```bash
python scripts/backup_restore_drill.py /path/to/nice-assistant-snapshot-....zip
```

The drill validates archive paths and manifest, copies only the database to a
temporary directory, runs `PRAGMA integrity_check`, upgrades that copy to the
current migration head, and reports the revision. It does not prove that an
operator retained the correct `NICE_ASSISTANT_MASTER_KEY`; actual rollback
acceptance must restart the prior image against a restored copy with the saved
key.

Record deployment-specific backup filenames and results only in the ignored
local acceptance record. Verification is non-destructive. Replacing the live
database or starting a prior image against a restored copy requires explicit
operator approval; retain the exact deployment master key and current archive
before beginning that drill.

Startup retention defaults are 30 days for archived generated audio, opted-in
STT recordings, and archived logs; `AUDIO_ARCHIVE_RETENTION_DAYS`,
`STT_RECORDING_RETENTION_DAYS`, and `LOG_ARCHIVE_RETENTION_DAYS` set the policy,
with zero disabling age pruning. `DAILY_DATABASE_BACKUP_LIMIT=14` and
`BACKUP_SNAPSHOT_LIMIT=10` are count limits. Review the admin storage report
before shortening retention because deletion is permanent outside backups.
Moving completed audio from the hot cache into the archive updates its durable
protected replay path; replay remains available until retention expires it.
