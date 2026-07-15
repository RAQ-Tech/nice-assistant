# Foundation-first roadmap

## Delivered foundation

1. Documentation and engineering guardrails — delivered.
2. Immediate correctness and repository hygiene — delivered.
3. Deterministic verification — delivered.
4. Typed data, migrations, and encrypted secrets — delivered.
5. ASGI transport migration — delivered.
6. Conversation, provider, and job service extraction — delivered.
7. Bounded causal conversation context — delivered.
8. Durable Memory v2 — delivered.
9. Typed, modular browser application — delivered.
14. Permissioned capability framework — delivered (voice steps 10–13 are
    intentionally deferred).
15. Platform Task Models — delivered.
16. Media model catalog and deterministic planner — delivered.
17. Persona visual identity persistence and validation — delivered.
18A. Truthful GPU capacity and admission coordination — delivered.
18B. Identity-aware generation workflows — delivered.
18C. Media editing and measured identity correction — delivered.
19. Production hardening and observability — delivered.

Step 6 removed the raw HTTP server and loopback bridge, added durable linked
turn/job state, provider-neutral chat/media contracts, streamed Ollama output,
bounded authenticated SSE, explicit cancellation, and direct `/api` compatibility
adapters. The current user input is now sent to the model exactly once.

Step 7 added per-chat causal execution, execution-time prompt planning, explicit
Ollama context allocation, bounded memory/history selection, append-only durable
summaries, context diagnostics, and truthful saved-memory behavior.

Step 8 added reviewable post-turn candidates, provenance and confidence,
auditable lifecycle/history, superseding edits, forget/undo, exact live-memory
constraints, active-only scoped FTS retrieval, and browser review controls.
Follow-up hardening separated reversible forget/hide from permanent deletion and
added atomic owner-scoped bulk memory and chat actions under ADR 0015.

Step 9 added a strict TypeScript/Vite browser with focused API, state, routing,
settings, chat, media, recording, playback, and visualization modules; canonical
SSE/job handling; Vitest and Playwright coverage; deterministic static packaging;
and browser source/build checks in the repository verifier. It removed the broad
legacy API after migrating saved media links to protected v1 artifact IDs.

Step 14 replaced media keyword/tag routing with typed capability requests, durable
owner-scoped capability requests and audit events, explicit approval/denial,
idempotent direct actions, linked cancellation/failure state, protected results,
and browser capability cards. It deliberately does not choose media models or
claim persona identity persistence.

Step 15 separated chat titles, conversation summaries, memory extraction, and
capability planning from persona behavior. It added typed structured-output
contracts, per-user role profiles, budgets/timeouts, health and model fallback,
content-free run audits, operator settings, developer-only evaluation cases, and
safe restart recovery. Persona models no longer receive platform tools.

Step 16 added an owner-managed catalog of model/LoRA/workflow resources,
controlled semantic metadata, compatibility and revisions, operator VRAM/LoRA
policy, deterministic explainable plans, approval-time stale-plan rejection,
real Automatic1111/ComfyUI LoRA forwarding, operator plan previews, and durable
manual-bypass disclosure. It deliberately does not claim live GPU residency,
adapter operations that are not implemented, or persona visual identity.

Step 17 added consent-bound persona identity profiles, normalized protected
references, provenance and review/deletion audit, encrypted LAN-verifier
settings, stateless CompreFace comparison, durable validation jobs/history,
truthful verified/rejected/unverified media states, and browser correction
flows. It deliberately does not alter generation or claim identity without a
passing comparison.

Step 18A removed the pretend residency layer and added provider-reported
capacity for ComfyUI, Automatic1111, and Ollama; disabled/observe/managed modes;
endpoint-bound exclusive-control authorization; verified coarse release;
non-blocking admission using catalog estimates; chat-priority serialization;
safe timeout/cancellation; durable audit; and an administrator settings surface.
It deliberately does not claim ownership of external clients or implement
identity-aware generation workflows.

Step 18B added platform-level `identity_control` intent guidance, explicit
ComfyUI reference-image bindings, active-consent and reviewed-reference gates,
immutable identity/profile/reference/workflow snapshots, approval-time digest
and revision checks, prompt plus reference composition, stage cancellation
checks, generated-media plan provenance, and truthful conditioned-but-unverified
browser/API state. It deliberately does not auto-validate, retry, inpaint, or
correct generated results.

Step 18C added real ComfyUI source/mask workflow bindings for explicit
image-to-image, inpaint, and outpaint jobs; durable per-plan attempt records;
inline post-generation identity comparison; bounded retries; optional
image-to-image correction graphs; and truthful verified, rejected, or
unverified results. Sequential stages use the maximum planned stage estimate
for admission rather than pretending their VRAM demand is simultaneous. The
task model remains generation-only until protected attachment resolution is a
real typed input, and live 12 GB timing/capacity tuning remains deployment
acceptance work.

Step 19 added the state-changing request header and origin boundary, strict and
HTTPS-secure cookie policy, bounded login lockout, private/Tailscale/explicit
provider URL policy, redacted JSON correlation logs, request/provider/job and
queue/storage metrics, readiness, configured retention, atomic artifact writes,
and temporary-copy backup integrity/migration drills. Deterministic failure tests
cover public/metadata targets, provider outage contracts, disk-full and empty
artifacts, corrupt snapshots, restart recovery, and clean lifecycle behavior.

## Deferred voice core

10. Blind TTS evaluation and provider decision.
11. Streaming, provider-neutral TTS v2.
12. Hybrid STT and turn detection.
13. Natural turn-taking and barge-in.

These steps remain valid, but TTS provider replacement is deferred while the
working Kokoro path remains available. They are not prerequisites for the media
and platform foundation below.

## Platform intelligence, media continuity, and release

20. **Real deployment acceptance — delivered.** The supported feature set was
    accepted on the Unraid/private-LAN topology with measured latency and VRAM
    behavior, safe provider-outage checks, live running-media cancellation,
    post-job ComfyUI reclamation, restart recovery, and backup verification.
    `docs/deployment-acceptance.md` is the public checklist; exact evidence is
    retained only in the ignored local operator record. The accepted managed
    policy authorizes only operator-confirmed exclusive media endpoints; shared
    providers remain outside release control. Realtime voice, destructive
    rollback, and visual identity remain explicitly separate acceptance work.

21. **Approachable settings experience — underway.** Step 21A is delivered for
    Visual Identity: guided reference setup, truthful generation-versus-
    verification readiness, owner-protected thumbnail pickers, fictional-persona
    rights language, and progressive disclosure for verifier diagnostics and
    destructive controls. Step 21B will apply the shared interaction rules to
    everyday tabs. Step 21C will make the operator tabs understandable without
    removing their advanced controls. See `docs/settings-experience.md` for the
    implementation-sized split.

Steps are delivered and reviewed independently. Step 11 cannot select providers
until a future listening decision is approved. Any future deployment acceptance
must use the intended LAN hardware and service topology.
