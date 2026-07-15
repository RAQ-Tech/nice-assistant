# Debt register

This register describes the current baseline. Update classifications in the same
change that alters them.

## Working and worth preserving

- Cookie-session authentication and first-user administration for private-LAN
  use, subject to the hardening items below.
- Owner-scoped chat, job, media, audio, and memory API coverage.
- Separate interactive and media job lanes.
- One dependency-injected FastAPI application with service/unit-of-work boundaries,
  durable linked conversation turns/jobs, safe provider failures, streamed Ollama
  chat, bounded SSE replay, and cooperative cancellation.
- Per-chat causal turn execution, provider-aware context budgets, exact saved
  memory deduplication, append-only summary checkpoints, and turn accounting.
- Review-first memory candidates, provenance, status/history, revision
  supersession, forget/undo, scope archival, and active-only scoped FTS retrieval.
- Strict TypeScript/Vite browser modules for API transport, state/routing,
  settings, chat/rendering, media, recording, playback, and visualization, with
  Vitest/Playwright coverage and deterministic generated assets.
- Platform-planned image/video capabilities with durable owner-scoped permission
  requests, explicit approval/denial, audit history, idempotent direct actions,
  linked jobs/cancellation, protected results, and future-turn tool outcomes.
- Separately configured platform Task Models for titles, summaries, memory
  candidates, and semantic capability planning, with strict JSON contracts,
  budgets, health/fallback, safe content-free run audits, and operator controls.
- Owner-managed media model/LoRA/workflow metadata, explicit compatibility,
  deterministic explainable selection, immutable approval plans, operator
  VRAM/LoRA limits, and truthful manual-generation bypass records.
- Consent-bound persona visual identity profiles, normalized protected
  references, explicit review/deletion, stateless LAN comparison, durable
  validation history, and truthful verified/rejected/unverified media state.
- Real ComfyUI, Automatic1111, and Ollama capacity/control adapters; disabled,
  observe, and explicitly authorized managed coordination; non-blocking
  catalog-estimate admission; chat-priority in-process serialization; and
  durable content-free resource audit.
- Consent-gated identity-aware ComfyUI generation with explicit operator-defined
  image bindings, immutable profile/reference/workflow provenance, stale-plan
  rejection, stage cancellation checks, and conditioned-but-unverified results.
- Explicit ComfyUI image-to-image, inpaint, and outpaint jobs with exact
  owner-scoped source/mask bindings; durable attempt provenance; automatic
  identity comparison; bounded correction/rerun; and failure-policy enforcement.
- SQLite backup snapshots, archive retention, provider readiness checks,
  process/container smoke foundations, and an administrator restore-drill
  action that reports database integrity and migration compatibility.
- Same-origin write enforcement, strict/optionally secure session cookies,
  bounded login lockout, private-LAN provider URL policy, redacted structured
  correlation logs, request/provider/job metrics, readiness and storage reports,
  configured sensitive-cache retention, atomic artifact writes, and temporary-copy
  backup migration drills. Audio hot-cache rotation updates durable protected
  replay paths instead of silently breaking completed synthesis links.
- OpenAI, Ollama, Automatic1111, ComfyUI, and Kokoro request implementations as
  migration inputs, not final architectural boundaries.

## Scaffold

- Some provider helper internals still use low-level HTTP/SQLite-shaped legacy
  inputs, but routes use SQLAlchemy repositories and unit-of-work boundaries.
- Provider-specific settings embedded directly in persona and UI records.
- Turn event replay is bounded and process-local, not a durable event log.
- Context token counts are conservative estimates before generation; actual
  Ollama prompt counts are captured when the provider returns them.
- Provider cancellation is cooperative; providers without interrupt support may
  finish work whose result is then discarded.
- Memory retrieval is lexical FTS plus recency; semantic retrieval remains an
  optional future interface rather than implied functionality.
- Rejected/forgotten memory retention is durable but does not yet have an
  administrator-approved automatic expiry policy. Users can permanently delete
  selected records, including their history, through explicit individual or
  atomic bulk actions.
- The first Task Model adapter is Ollama only. Cloud or additional LAN task
  providers must implement the same structured-output contract before being
  advertised.
- Developer screening checks typed and semantic task behavior, but final model
  selection still requires live latency/quality evaluation on the Unraid GPU.
- Capability intent remains a probabilistic Task Model decision outside the
  narrow deterministic permission boundaries. Literal text-only response
  contracts are blocked before planning, but broader precision still requires
  representative live screening and monitoring rather than keyword routing.
- Media VRAM/load values remain operator estimates of demand. Provider telemetry
  now measures available capacity but cannot infer a pending model's demand.
  Direct media buttons still use legacy provider settings through a disclosed
  manual plan and have unknown demand, so they bypass measured-capacity
  admission. They do participate in the shared-resource lease and authorized
  managed post-job media reclamation.
- ComfyUI editing is explicit-only. The Task Model cannot yet resolve protected
  chat attachments into source/mask media IDs and therefore advertises only
  generation. Automatic1111 remains generation-only.
- CompreFace verification is stateless and replaceable, but connection-attempt
  cancellation remains bounded by its timeout. The global provider policy now
  restricts its configured base URL; the separately operated service remains a
  trusted deployment component.
- ComfyUI identity cancellation closes active responses where possible, but a
  provider request may remain bounded by timeout and uploaded input retention is
  owned by the separate ComfyUI service.

## Placeholder or unimplemented

- Realtime/streaming TTS; no endpoint is advertised until Step 11 implements it.
- Local STT; the setting is retained for migration compatibility but disabled in
  the UI until an adapter exists.
- Realtime turn detection, partial transcripts, barge-in, and speech fallback.
- Multi-reference fusion and automatic mask creation. Identity-stage latency and
  capacity are unaccepted until the real verifier, consented references, and a
  compatible ComfyUI identity workflow are deployed; the completed Step 20 base
  media checks are not substitute evidence.

## Misleading or broken foundations

- Current TTS supports corrected request/response generation and basic OpenAI
  voice direction, but not the streaming, cancellation, fallback, or evaluation
  behavior needed by the voice-first target.
- Login throttling and metrics are in-process because the supported deployment
  is one private-LAN application process. A future multi-replica/public design
  would require shared rate-limit and telemetry infrastructure and a new threat
  model.
