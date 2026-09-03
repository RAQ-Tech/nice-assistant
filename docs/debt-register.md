# Debt register

This register describes the current baseline. Update classifications in the same
change that alters them.

## Working and worth preserving

- Cookie-session authentication and first-user administration for private-LAN
  use, subject to the hardening items below.
- Owner-scoped chat, job, media, audio, and memory API coverage.
- A persona's voice settings are keyed by provider rather than stored in columns
  named after one. Nine text-to-speech columns became one object holding the
  providers a persona has an opinion about, plus an optional `default` any
  provider falls back to. A provider added later needs no migration and no
  column of its own.
- The persistence boundary is consistent: every module above `app/database.py`
  reads and writes through SQLAlchemy repositories and unit-of-work boundaries.
  The helpers that still took sqlite3 rows, raw connections, and JSON strings
  were unreachable from the application and were deleted rather than converted,
  since converting them would have produced repository-shaped helpers nothing
  calls. `app/database.py` remains low-level on purpose: it runs migrations and
  the startup sweep before any session exists.
- Separate interactive and media job lanes.
- Turn event replay is bounded and process-local by decision, not by omission.
  ADR 0034 records why: reconnects are correct regardless, because the snapshot
  carries the sequence its text covers, so a subscriber neither replays deltas
  twice nor silently misses evicted ones. A restart still ends an unfinished
  turn rather than resuming it, and a durable log would offer replay of turns
  that no longer exist. The single-process assumption it rests on is enforced at
  startup rather than documented, in `app/single_process.py`.
- One dependency-injected FastAPI application with service/unit-of-work boundaries,
  durable linked conversation turns/jobs, safe provider failures, streamed Ollama
  chat, bounded SSE replay, and cooperative cancellation.
- Per-chat causal turn execution, provider-aware context budgets, exact saved
  memory deduplication, append-only summary checkpoints, and turn accounting.
- Review-first memory candidates, provenance, status/history, revision
  supersession, forget/undo, scope archival, active-only scoped FTS retrieval,
  and editable pending proposals from the chat transcript.
- Strict TypeScript/Vite browser modules for API transport, state/routing,
  settings, chat/rendering, media, recording, playback, and visualization, with
  Vitest/Playwright coverage and deterministic generated assets.
- Guided Visual Identity settings with separate truthful reference, generation,
  missing-conditioning policy, comparison readiness, and measured-failure
  policy; owner-protected thumbnail pickers; and optional advanced diagnostics
  instead of opaque media-ID inputs.
- Goal-oriented everyday settings for General, speech, direct media defaults,
  Memory, User, Personas, and Workspaces, with accessible information tips and
  closed-by-default advanced provider controls.
- Goal-oriented operator settings for Models, Task Models, Media Catalog, GPU
  Coordination, and Data, with effective-state summaries, collapsed named
  editors, real per-model overrides, explicit persistence, and visible safety
  consequences. Focused typed views own each operator workflow.
- Platform-planned image/video capabilities with durable owner-scoped permission
  requests, explicit approval/denial, audit history, idempotent direct actions,
  linked jobs/cancellation, protected results, and future-turn tool outcomes.
- Separately configured platform Task Models for titles, summaries, memory
  candidates, and semantic capability planning, with strict JSON contracts,
  budgets, health/fallback, safe content-free run audits, and operator controls.
- Independent post-reply title, capability, and memory jobs with durable IDs,
  truthful media-claim guarding, progressive chat controls, and cancellation UI
  scoped to work that is still cancelable.
- Owner-managed media model/LoRA/workflow metadata, explicit compatibility,
  deterministic explainable selection, immutable execution plans, operator
  VRAM/LoRA limits, and truthful manual-generation bypass records.
- Late media-provider enablement now bootstraps only an empty catalog kind and
  migration `0015` repairs affected accounts; provider settings do not overwrite
  operator-curated resources.
- Typed persona-subject planning prevents identity requirements from leaking into
  unrelated images. Persona plans prefer reviewed-reference conditioning, use a
  disclosed unconditioned fallback only when the saved policy permits it, and
  expose targeted setup/recheck actions when strict planning blocks. Identity
  extension/model installation remains deployment-owned; guided graph import,
  provider-schema inspection, and exact binding are delivered in the product.
- Authored persona character cards in the protected prompt section, capped when
  saved rather than when a turn is planned, with the cap taken against the
  narrowest configured context window and a live cost meter in the editor.
- A conversation history floor. Optional prompt sections yield in reverse
  authority order before the conversation is starved, nothing yields when there
  is no history to protect, and a turn that dropped sections is marked degraded
  and reports the material as omitted. Persona example dialogue rides on that
  floor as whole `<START>`-delimited exchanges.
- Persona lorebooks with deterministic platform-owned keyword matching, literal
  keys, a bounded scan window, no recursive activation, priority-ordered whole
  entries, and an owner-scoped preview route that reports what fires and what
  fits.
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
- Conversational editing of an image already in the chat, using opaque
  platform-published attachment references that the model may select but never
  invent, re-resolved from the same owner-scoped query before execution, offered
  only when a ready editing operation and a resolvable image both exist, and
  always confirmed by the owner before it runs.
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

- Eight functions exceed the cyclomatic complexity ceiling of 15 and are
  grandfathered with `# noqa: C901`. `create_turn` and `ContextService.plan`,
  the two on the conversation critical path, are no longer among them. The
  markers are the list; the ceiling stops it growing, and nothing since has
  added one.

- `workspace_id` and `persona_id` remain accepted on `POST /chats/{id}/turns`
  after ADR 0032 made them redundant. They are refused when they disagree with
  the chat, so they cannot cause harm; removing them is a breaking API change
  that is not scheduled.
- Context token counts are conservative estimates before generation; actual
  Ollama prompt counts are captured when the provider returns them.
- Provider cancellation is cooperative; providers without interrupt support may
  finish work whose result is then discarded.
- Memory retrieval is lexical FTS plus recency; semantic retrieval remains an
  optional future interface rather than implied functionality.
- Rejected/forgotten memory retention is durable and unbounded by default. An
  operator may set `MEMORY_DISCARD_RETENTION_DAYS` to expire them automatically;
  it stays off unless configured so an upgrade never deletes content a user only
  hid. Explicit individual and atomic bulk deletion remain available.
- Task adapters are Ollama and OpenAI. The second one exists to keep the
  structured-output contract provider-neutral; further providers must implement the
  same contract before being advertised. The OpenAI adapter serves task roles only
  and is deliberately not offered for persona conversation, nor selectable in the
  Task Model settings UI. Readiness now reports adapter installation, account
  credentials, and live verification as three separate facts, and never claims
  the third.
- Developer screening checks typed and semantic task behavior, but final model
  selection still requires live latency/quality evaluation on the Unraid GPU.
- Deterministic human-experience scenarios cover critical contracts, but emotional
  tone and speech quality still require a future operator-approved listening
  evaluation; tests do not claim subjective voice quality.
- The immutable deployment-guard bundle foundation is implemented, but the
  existing private server still runs the legacy direct-guard enrollment until
  its one-time supervised launcher migration and live rollback/re-update
  acceptance complete.
- Capability intent remains a probabilistic Task Model decision outside the
  narrow deterministic permission boundaries. Literal text-only response
  contracts are blocked before planning, but broader precision still requires
  representative live screening and monitoring rather than keyword routing.
- The automatic image boundary now has a conservative deterministic action gate
  and curated negative tests. Natural-language coverage is intentionally biased
  toward false negatives; expanding accepted phrasing requires evaluation rather
  than weakening the story/discussion guard. Editing uses a separate gate with
  its own curated negatives so the auto-run creation boundary is unchanged; an
  edit is only ever proposed for confirmation, never run unattended.
- Media VRAM/load values remain operator estimates of demand. Provider telemetry
  now measures available capacity but cannot infer a pending model's demand.
  Direct media buttons still use legacy provider settings through a disclosed
  manual plan, so the coordinator does not select for them, but their demand is
  no longer unknown: the model they name is matched in the catalog and its
  recorded estimate is carried onto the plan, which puts them under
  measured-capacity admission alongside conversational requests. A model the
  catalog has never seen still has unknown demand, and the plan says so rather
  than guessing. What remains split between the two paths is selection, not
  admission.
- ComfyUI editing is now reachable from conversation through platform-published
  attachment references under ADR 0029, but only for images in the current chat
  and only with owner confirmation. The task model never receives or supplies a
  media ID. Automatic1111 remains generation-only, mask creation is still
  manual, and the edit action gate is deliberately biased toward false
  negatives, so accepted phrasing expands by evaluation rather than by widening
  the pattern.
- CompreFace verification is stateless and replaceable, but connection-attempt
  cancellation remains bounded by its timeout. The global provider policy now
  restricts its configured base URL; the separately operated service remains a
  trusted deployment component.
- ComfyUI identity cancellation closes active responses where possible, but a
  provider request may remain bounded by timeout and uploaded input retention is
  owned by the separate ComfyUI service.

## Placeholder or unimplemented

- Partial transcripts, and speech provider fallback chains. Streaming speech
  (ADR 0037), interruption that stops the provider work (ADR 0036), and
  end-of-turn detection (ADR 0038) are implemented against the local Kokoro
  path. Transcription at natural pauses shipped on 2026-08-18 (ADR 0041) and is
  off by default. What remains deferred is partial results refined as more audio
  arrives, which no transcription service reachable from here offers. There is
  no fallback chain by decision: Kokoro is the voice, and there is nothing
  local to fall back to.
- Lorebook matching is literal keywords plus common English plurals over a
  three-message window, so it will still miss a paraphrase that shares no key. This is a deliberate trade for predictable,
  debuggable behavior with no embedding model or extra service; the preview route
  exists to make the resulting authoring work tractable. Semantic matching remains
  an optional future interface rather than implied functionality.
- Lore is persona-scoped. Sharing an entry across several personas in a workspace
  means authoring it more than once until a sharing model is designed.
- Multi-reference fusion and automatic mask creation. Identity-stage latency and
  capacity are unaccepted until the real verifier, consented references, and a
  compatible ComfyUI identity workflow are deployed; the completed Step 20 base
  media checks are not substitute evidence.

## Misleading or broken foundations

- An operator-imported ComfyUI workflow cannot receive the request prompt. The
  executor writes the positive prompt into node `"6"` and the negative into node
  `"7"` of a fixed nine-node graph, then merges the operator's inline graph over
  it. Bindings exist for identity, source, and mask images but not for prompt,
  seed, or dimensions, so an imported graph either loses the prompt or renders
  the text baked in at export. It still returns an image, so the failure is
  silent. Fixed by the image generation program in `BACKLOG.md`; see ADR 0030.
- Prompt construction for local backends is hardcoded and global: a fixed
  quality prefix on every prompt and one negative string that varies only on the
  NSFW toggle. This is wrong for checkpoint families that expect score or booru
  tags and for those that support no negative prompt. See ADR 0030.
- Capability planning sees only the current user message, so a request that
  refers to something established earlier in the conversation cannot be routed
  or described correctly. See ADR 0030.
- Picture-to-words alignment is ranking over shared words, which is as crude as
  the library matching it sits beside. It only reorders candidates the user's
  own request already produced, so being wrong costs a less apt picture rather
  than an unrelated one. See ADR 0033.
- Identity resemblance currently leans on generate-then-compare correction
  (ADR 0013). That is a check standing in for a control: it spends latency
  resampling and can reject every candidate, and it makes an optional service
  load-bearing. ADR 0031 redirects resemblance to a declared structural
  mechanism and demotes comparison to advisory measurement.
- Login throttling and metrics are in-process because the supported deployment
  is one private-LAN application process. A future multi-replica/public design
  would require shared rate-limit and telemetry infrastructure and a new threat
  model.
