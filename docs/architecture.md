# Architecture

## Target shape

```text
Browser (HTTPS)
  |-- typed HTTP API (/api/v1)
  `-- realtime WebSocket (/api/v1/realtime)
             |
        ASGI application
             |
  +----------+-----------+-------------+
  | conversation service | capability  |
  | task model service   | memory/jobs |
  +----------+-----------+-------------+
             |
  +----------+-----------+-------------+
  | LLM providers | speech providers   |
  | media providers | persistence      |
  +----------+-----------+-------------+
             |
      separate LAN/cloud services
```

Nice Assistant owns conversation state, permissions, persistence, provider
selection, fallback policy, and browser session events. Provider services own
their model processes and hardware lifecycle.

## Required boundaries

- Routes validate transport input and delegate; they do not contain provider or
  persistence workflows.
- Application services contain use cases and transaction boundaries.
- Provider adapters normalize health, timeout, cancellation, streaming, and
  error behavior.
- Persistence uses explicit schema migrations and relational constraints.
- Alembic owns schema versions; SQLAlchemy models define the target schema. Typed
  setting rows are canonical and the
  legacy JSON column is retained only as a temporary downgrade bridge.
- Browser API, state, rendering, recording, playback, and visualization code are
  separate modules connected through typed events.

## Implemented service graph

`app.asgi.create_app` builds one configured application and one dependency-injected
service graph. `ResourceService` owns authenticated resources, `ConversationService`
owns turn preparation and assistant persistence, `JobService` owns queue/state/
cancellation, and `MediaService`, `SpeechService`, `ProviderService`, and
`OperationsService` own their respective workflows. `ContextService` owns causal
prompt planning, budgets, saved-memory selection, compaction, and accounting.
`MemoryService` owns candidate extraction, review transitions, revision history,
scope archival, explicit permanent deletion, atomic owner-scoped bulk actions,
and FTS retrieval policy. `ConversationService` likewise separates chat hiding
from permanent deletion and rejects destructive deletion while linked work is
active. See ADR 0015.
`CapabilityService` owns the registry, durable permission requests, approval,
denial, idempotency, audit events, linked job submission, and terminal results.
`TaskModelService` owns separately configured title, summary, memory-extraction,
and capability-planning roles, strict structured outputs, budgets, readiness,
fallback, and content-free run audit records.
`MediaCatalogService` owns resource metadata, compatibility, deterministic plan
construction, immutable reviewed snapshots, explicit rechecks of never-ready
blocked plans, and approval-time resource revalidation; the pure selection
policy is isolated in `media_planner.py`.
Capability Task Models declare whether the requested image actually depicts the
selected persona. The platform derives the hard `identity_control` feature from
that typed decision so persona-model prose cannot make unrelated images require
identity conditioning. See ADR 0017.
`ResourceService` delegates the narrow disabled-to-enabled media transition to
`MediaCatalogService`: a starter model is created only when that catalog kind is
empty. Existing operator resources remain authoritative and are never synced
from direct-action settings. See ADR 0016.
`IdentityService` owns consent-bound persona identity profiles, normalized
reference storage, review/deletion audit, queued comparisons, and truthful media
claim state. `IdentityVerificationProvider` is a separate stateless LAN-service
boundary; the initial CompreFace adapter does not enroll provider-side subjects.
For `identity_control` plans, `MediaCatalogService` first prefers the chat
persona's reviewed profile/reference snapshot and exact ComfyUI workflow inputs.
If only conditioning configuration is unavailable and the snapshotted policy
allows it, the service creates a disclosed `unconditioned` plan instead; consent,
reference-integrity, and stale-revision failures do not fall back. The
ComfyUI adapter uploads reference/source/mask images and injects only declared
inputs. `MediaService` links every candidate to the immutable plan, invokes the
same `IdentityService` comparison rule inline, and durably records bounded
correction attempts. Rejected candidates are not returned under `block_claim`.
`ResourceCoordinator` owns provider capacity snapshots, endpoint-bound release
authorization, catalog-estimate admission, one in-process shared-resource lease,
chat priority, bounded waits, and content-free audit. ComfyUI, Automatic1111,
and Ollama resource adapters use real provider endpoints; they never infer
residency from application settings. Sequential generation/correction stages use
the maximum stage estimate for admission, not the sum of mutually exclusive
stages. Unknown-demand local image work is not falsely capacity-approved, but it
still holds the lease; managed mode keeps that lease closed while it reclaims an
explicitly authorized media provider after the job.
Routes do not execute provider
work or direct SQL.

`SecurityObservabilityMiddleware` is the outer HTTP boundary. It assigns or
validates a request correlation ID, enforces the write-header/origin policy,
adds browser security headers, and records bounded status/latency metrics.
`LoginThrottle` and `ProviderUrlPolicy` are dependency-injected policies used by
the login and every user-configurable outbound provider path. `MetricsRegistry`
contains only counts/timing; `OperationsService` adds readiness, queue/storage
reporting, retention, and temporary-copy backup verification. See ADR 0014.

SQLAlchemy repositories are accessed through a unit of work. A user message,
queued `conversation_turn`, and linked `async_job` are created atomically. The
job and turn move through `queued`, `running`, and one terminal state together;
assistant messages are persisted only after successful provider completion.
On startup, unfinished jobs and turns become failed with the safe message
`interrupted by server restart`.

Persona chat requests do not receive tools. After the persona reply, the typed
capability-planning role may propose controlled semantic requirements. The
deterministic catalog coordinator selects only explicitly described and
compatible resources. A high-confidence ordinary image action uses audited
`auto` permission under the saved default; `always_ask`, video, and consequential
capabilities remain confirmation-gated. The assistant reply is durable before
nonessential planning, and every chat media request creates a durable attachment
on an assistant message. Capability/job/attachment callbacks share a unit of
work so running, completion, failure, and cancellation cannot disagree. Direct
browser actions use the same capability and attachment services with explicit
permission and a truthfully labeled manual plan. See ADRs 0007–0009, 0019–0020
and `docs/media-catalog.md`.

Turns within one chat have a durable sequence and a queue ordering key. Prompts
are built when work starts so a successor sees its completed predecessor while
later submitted messages remain outside its boundary. Append-only conversation
summaries compact old transcript prefixes; see `docs/conversation-context.md`.

Eligible completed turns atomically create a durable memory-extraction job. The
job uses its separately configured task role after turn completion and may
create pending candidates with source provenance. Long-chat compaction likewise
uses the summary task role. Only active memories cross the retrieval boundary;
see `docs/memory.md` and `docs/task-models.md`.

`ChatModelProvider` and `MediaProvider` normalize health, timeouts, cancellation,
artifacts, and safe failures. Ollama implements streamed `/api/chat` NDJSON. The
media implementations are adapters over existing behavior and do not use the
legacy modeled-residency layer to make readiness claims.

The capability layer is intentionally an execution/permission boundary. The
Task Model accepts semantic media intent only and cannot see resource identities.
The media catalog coordinator owns checkpoint/LoRA/workflow selection using
typed metadata, explicit compatibility, revisions, and an operator VRAM budget.
Configured VRAM remains an estimate of job demand rather than a
residency/readiness claim. When resource coordination is enabled, measured free
capacity gates catalog-planned local image work against that estimate. Managed
release requires endpoint-specific exclusive authorization and is remeasured;
the same authorization permits post-job media reclamation before a waiting chat
can start. The process lease cannot serialize external clients. ComfyUI editing uses
explicit owner-selected source/mask IDs; the task model stays generation-only
until attachments have a typed resolver. See `docs/persona-visual-identity.md`
and ADRs 0010–0013.

## Compatibility

All application contracts use `/api/v1`. FastAPI/Uvicorn owns the only public
socket and the TypeScript browser calls the typed routes directly. The raw
handler, loopback proxy, bridge flag, second listener, and broad `/api`
compatibility router are removed. Migration `0007_browser_v1_cutover` rewrites
stored legacy image/video links to owner-protected `/api/v1/media/{id}` URLs so
history remains usable after the route removal.
Migration `0008_capability_framework` adds capability requests/events and a
nullable one-to-one capability link on async jobs.
Migration `0009_task_models` adds owner-scoped role profiles and content-free
run audits, seeding existing users from their prior global model when available.
Migration `0010_media_catalog` adds owner-scoped resources, compatibility,
catalog settings, and durable plans while importing enabled legacy provider
settings.
Migration `0011_persona_identity` adds encrypted verifier settings, visual
identity profiles, references, validation records, and content-free audit events
without reconstructing existing persona, media, or job tables.
Migration `0012_resource_coordination` adds the singleton coordination policy,
endpoint-fingerprint control authorizations, and content-free resource audit
events without reconstructing existing job or media tables.
Migration `0013_identity_generation` adds immutable identity snapshot fields to
media plans and a nullable generated-media plan link while preserving existing
plans and artifacts.
Migration `0014_media_correction_workflows` adds owner-scoped durable generation,
validation, and correction attempt records without reconstructing existing
conversation, plan, job, or media tables.
Migration `0015_media_provider_bootstrap` repairs only missing starter catalog
kinds for already-enabled providers and does not alter existing resources.
Migration `0016_identity_fallback` adds the explicit conditioning-unavailable
policy to existing persona visual-identity profiles without changing references,
media, or completed plans.
Migration `0017_chat_attachments` adds linked retry metadata, effective automatic
permission auditing, user image preferences, and reload-safe chat attachments
without reconstructing the referenced capability table or losing dependent rows.

## Browser application

Vite compiles strict TypeScript from `frontend/src` into deterministic static
assets under `web`; the Python wheel and container serve only those generated
assets. API transport, phase state, hash routing, settings, chat orchestration,
rendering, media, recording, playback, and visualization have focused module
boundaries. `ClientStateMachine` rejects illegal UI phase transitions. Turn
stream disconnect and cancellation remain distinct: SSE may reconnect/poll,
while only an explicit job DELETE cancels durable work. See
`docs/browser-architecture.md` and ADR 0006.
Everyday settings are isolated from operator settings and use shared accessible
controls, information tips, and closed advanced disclosure. Focused Models,
Task Models, Media Catalog, and Operations views own their typed interaction and
API workflows; the settings shell only composes them. Provider execution remains
outside the view layer.
The default chat shell uses the same progressive-disclosure rule: persona,
conversation, speech, and image-blur controls are primary; workspace, model,
memory, client-state, and visualization controls remain available in chat details.
The focused visual-identity settings module provides consent, reference review,
separate generation-unavailable and comparison-failure policy controls, provider
readiness, validation, and correction history without moving biometric decisions
into general browser state or persona prompts. The Media Catalog view owns the
guided ComfyUI workflow import/inspection/binding interaction, while only the
provider service talks to `/object_info`.

## Turn events and cancellation

`GET /api/v1/turns/{turn_id}/events` sends a current `turn.snapshot` before live
events. Event IDs support `Last-Event-ID` replay from a bounded in-process buffer.
The event sequence is `turn.queued`, `turn.started`, zero or more
`assistant.delta`, then exactly one of `turn.completed`, `turn.failed`, or
`turn.cancelled`. A disconnected SSE client does not cancel work. Completed
buffers expire after a short window; durable state remains available through
the turn and job endpoints.

Cancellation is cooperative. Queued work is removed from its lane. Running work
receives a cancellation token, and cancellable HTTP adapters close their response.
Adapters that cannot interrupt immediately may continue outside the durable turn,
but their late results are discarded. Bidirectional voice cancellation remains a
Step 13 WebSocket concern.

Capability audit events are durable; SSE turn events remain process-local.
Disconnecting the browser does not approve, deny, or cancel a capability.

After the assistant message is durable, title generation, capability planning,
and memory extraction run as independent jobs. Result payloads expose named IDs
plus `followup_job_ids`; the legacy `followup_job_id` is retained during consumer
migration. A failure in one follow-up cannot rewrite a completed turn. Explicit
image actions buffer persona prose through a deterministic premature-claim guard
that also recognizes terse status phrases, bare media placeholders, and
demonstrative delivery wording such as `Image sent`, `[Image]`, or `Here is that
picture`. It discards completion-dependent remnants when no truthful future
intent remains before publishing them, while ordinary non-media turns retain
token streaming.

## Realtime direction

Ordinary CRUD remains HTTP. Text generation streams server events. A future single
authenticated WebSocket carries realtime microphone, transcript, model-token,
speech, cancellation, and state events. The server remains authoritative about
turn identity; the browser remains authoritative about how much audio actually
played.
