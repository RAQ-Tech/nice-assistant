# Testing and acceptance

## Local verification order

1. Run focused unit or contract tests for the changed subsystem.
2. Run the complete Python and browser suites.
3. Run the process smoke check.
4. Run the container or live-provider check when the change affects deployment
   or provider integration.

The full suite must be repeatable without leaked servers, threads, files,
databases, ports, or global module state. Foundation changes require three
consecutive full-suite passes.

The canonical command is `python scripts/verify.py`; use `--repeat 3` for a
foundation change. Node/npm are required. The command runs strict TypeScript
checking, Vitest, a clean Vite production build, Python compilation/static/
formatting checks, coverage plus the Python suite, the process smoke, and
Playwright browser journeys.
Branch coverage is enforced at a minimum of 70 percent across `app`; no legacy
server exclusion remains.

Verification also runs `scripts/audit_public_repo.py`. On an operator
workstation, maintain `.local/public-repo-private-values.txt` with one private
literal per line so the audit catches accidental reintroduction without sending
the watchlist to GitHub. The audit reports only file, line, and finding type; it
does not echo matched content into CI logs.

Deployment-runtime changes also require building the image and starting a
container from it. A successful image build is insufficient because entrypoint,
installed dependency, port, and lifespan failures occur only at container
startup.

## Test layers

- Unit tests cover pure parsing, policy, state machines, and error normalization.
- Production-hardening tests cover CSRF/origin behavior, login lockout,
  secure-cookie flags, private/Tailscale/provider allowlists, metadata/public
  target rejection, correlation/security headers, readiness/admin isolation,
  queue/storage metrics, configured retention, atomic disk-full writes, empty
  artifacts, and corrupt/valid backup restore drills.
- API tests use isolated temporary databases and deterministic fake providers.
- Migration tests upgrade pre-0004/0005/0007/0008/0009/0010/0011/0012/0013/0014 databases and prove
  chats, messages, jobs, media, memories, turn ordering, stored artifact links,
  Task Model profiles, and imported catalog resources survive.
- Persona identity tests cover explicit consent, safe image normalization,
  pending/approved/rejected/deleted references, encrypted verifier credentials,
  protected owner-scoped delivery, passed/below-threshold claims, provider
  errors, cancellation/deletion, restart recovery, and audit history.
- Identity-generation tests cover exact ComfyUI reference/source/mask binding validation and multipart
  upload, persona/profile/reference gates, file digests, stale approval,
  appearance prompt composition, immutable generated-media plan provenance,
  stage cancellation checks, measured failed/passed correction attempts,
  failure policy, and verified/unverified API/browser labels.
- Provider contract tests exercise the same behavioral suite for every adapter.
- Ollama tests cover fragmented NDJSON, completion metadata, mid-stream errors,
  malformed frames, timeout/unavailable behavior, and cancellation closure.
- Turn tests cover legal transitions, atomic linked state, safe failures,
  idempotent cancellation, snapshot-first SSE, bounded replay, terminal ordering,
  and owner isolation.
- Capability tests cover the legal transition matrix, semantic tool schemas,
  approval/denial, explicit-action idempotency and mismatch conflicts, audit
  order, owner isolation, linked completion/failure/cancellation, late-artifact
  discard, protected delivery, restart recovery, and future-turn tool context.
- Ollama tests also cover tool payloads, parsed calls, and malformed arguments.
- Task Model tests cover strict schemas, distinct persona/task models, budgets,
  readiness, fallback, safe errors, owner isolation, content-free audits,
  restart recovery, controlled semantic vocabularies, and the prohibition on
  media resource selection. They also prove that an explicit literal text-only
  response contract cannot reach capability planning, while a preceding real
  media request is not hidden by a later formatting clause. A curated
  developer evaluation suite screens title specificity, summary retention,
  memory inclusion/exclusion, and capability precision.
- Media catalog tests cover CRUD and owner isolation, relational compatibility,
  deterministic metadata selection despite misleading filenames, priority and
  VRAM policy, immutable revisions, blocked adapter operations, stale-plan
  rejection, manual bypass disclosure, and selected settings/LoRA payloads.
- Resource-coordination tests cover real provider response parsing, unknown and
  unavailable telemetry, admin isolation, disabled/observe/managed policy,
  endpoint-fingerprint authorization, verified release, safe timeout,
  cancellation, durable content-free audit, non-blocking media admission, and
  chat-priority serialization. They also prove that unknown-demand local image
  work receives no false capacity admission, managed post-job cleanup retains
  the lease until release finishes, observe mode never releases, and work
  cancelled before execution cannot release a provider while running
  cancellation still performs post-provider cleanup exactly once. Deterministic
  fakes replace live GPU services in CI.
- Context tests cover multi-worker causal ordering, independent chats, explicit
  provider allocation, budget accounting, exact memory deduplication, oversized
  protected content, durable summaries, and degraded summary fallback.
- Memory tests cover legacy data migration, exact-duplicate supersession, FTS
  population/ranking, active-only scoped retrieval, nonblocking extraction,
  provenance, review transitions, superseding edits, forget/undo, extraction
  failure, secret-like candidate rejection, forget-versus-delete semantics,
  permanent history/FTS removal, atomic bulk actions, canonical routes, and owner
  isolation. Chat data-action tests distinguish bulk hide from permanent delete.
- Vitest covers the phase machine, settings normalization, canonical API/error
  behavior, fragmented SSE parsing, protected media rendering, routing, and safe
  markdown, capability approval/denial state, Task Model settings/audits, and
  media catalog planning, GPU coordination controls/status, and canonical
  multipart identity-reference transport, and memory selection/bulk-action
  confirmation behavior.
  Direct-LAN client-ID coverage proves that chat does not require the
  secure-context-only `crypto.randomUUID`; Data settings coverage exercises the
  administrator backup verification action and visible restore-drill result.
  Task Model and Media Catalog settings tests must preserve unsaved edits when
  refresh responses finish late.
  Playwright waits for and inspects mutation requests rather than inferring a
  completed save from unrelated visible state. Playwright deterministically
  covers onboarding/login, streamed chat, settings, memory review, and media
  while rejecting legacy API calls. It also checks computed input/select and
  native option colors in both themes so browser-default light controls cannot
  make dark-theme text unreadable. A direct-media journey proves that active
  work exposes an enabled cancellation control, calls the canonical job DELETE
  endpoint, returns to `idle`, and does not misreport acknowledged cancellation
  as an error.
- Deployment acceptance exercises real hardware, HTTPS microphone access,
  provider fallback, restart recovery, and backup restore.

Live credentials are never required by CI. Live checks must be opt-in and must
redact request data and provider errors.

The process smoke starts a real Uvicorn process and a deterministic fake Ollama.
It verifies health, login, provider readiness, streamed chat/job completion,
queued and running cancellation, owner-protected media, backups, and process
shutdown through canonical APIs, including proof that the legacy media route is
absent. Every state-changing smoke call carries the production CSRF marker. The
container smoke repeats the installed-package path from the built
image through `scripts/container_smoke_check.ps1`. It verifies task-profile
migration/readiness, chat and documented task fallback, cancellation, protected
media, consent-bound identity reference normalization/review/deletion, truthful
disabled-verifier readiness, the installed `0013` migration, durable
identity-conditioned capability planning, and clean shutdown. The planning smoke
stops before provider execution; real ComfyUI workflow and identity-provider
hardware acceptance remain separate explicit checks.
