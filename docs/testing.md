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
Playwright browser journeys. It then runs the deterministic human-experience
scenario subset. Run that subset directly with
`python scripts/evaluate_human_experience.py` or `npm run evaluate:human`.
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

Deployment-guard changes additionally run `tests/test_deployment_guard.py`.
Launcher coverage includes shell syntax; exact action arity and injection
rejection; running-digest, repository, source, revision, and downgrade policy;
raw manifest hashes and installed modes; manifest path/type/size/link contracts;
non-executing extraction; independent payload and canonical configuration
comparison; shared-lock and sanitized delegation; atomic current/previous
switching; exact interrupted-update cleanup; legacy key-migration ordering; and
the PowerShell update/rollback contract. The executable fake-Docker/Linux
harness covers sanitized delegation, bootstrap/update, mixed-case provenance,
wrong digest and mode rejection, stopped-helper cleanup, and interrupted pointer
recovery. Hostile-file and installer-interruption cases outside the bounded
root-capable simulations below remain static contracts plus required live
acceptance; they are not claimed as executable simulations.
Installer contracts also pin the only symlink exception to stock Unraid's
root-owned `/root/.ssh -> /boot/config/ssh/root` layout, including the exact
VFAT mount, restrictive masks, resolved ancestry, replacement probe,
compare-before-switch behavior, and root-only recovery. Root-capable executable
tests cover canonical success, duplicate-marker collapse, pre-switch failure,
fresh installation with no prior authorization file, the relocated
stock-Unraid branch, wrong-target rejection, and final revalidation after a
target swap. They also inject `TERM` before and at the commit boundary, a
rename that completes before reporting failure, post-rename validation
failure for both existing and absent authorization files, an unexpected
concurrent post-switch edit, and a recovery flush failure. Those cases prove
that the installer either restores the original authorization, removes a
newly created authorization, or preserves both the ambiguous live file and
its verified recovery copy without claiming success. Live Unraid enrollment
must still exercise the real VFAT branch and replacement/retired-key checks
before enrollment is considered accepted.

Container verification also proves `.local`, environment files, the dedicated
deployment-key filename, and ignored remote configuration are absent from the
installed image. A clean public-repository audit alone does not prove build
context privacy.

Guard release review must also prove that `bundle_version` increased whenever
the guard program, either jq filter, or manifest metadata changed from the
previous published bundle, and that the final manifest hashes match the exact
LF bytes shipped in the image. Equal-version content changes are a release
error even though the launcher safely rejects them at installation time.

Definition-probe fixtures treat MAC intent as persisted policy rather than
runtime inference. Under the default
`NICE_DEPLOY_PRESERVE_EXPLICIT_MAC=false`, a live definition with a
Docker-generated endpoint MAC and either an absent or nonempty deprecated
`Config.MacAddress` projection produces a payload with neither MAC field.
Different generated values normalize equally across a second recreation;
`Config.MacAddress` is always removed.

Explicit-policy fixtures set the root-owned value to `true` through the
`--preserve-explicit-mac` enrollment switch and prove that one nonempty endpoint
MAC is preserved and comparison-gated. They also prove that an endpoint
mismatch, malformed policy, zero or multiple endpoints, empty MAC, and conflict
with a nonempty legacy `Config.MacAddress` fail closed. The same policy must
reach the launcher-owned builder/comparator, candidate filters, delegated guard,
validate-definition, deploy acceptance, and definition-based rollback. Bundle
review asserts version 2 and final LF-byte hashes for the guard and both jq
filters. Launcher tests additionally select version 1, prove application
deploy/rollback are refused while inspection remains available, and reselect
version 2 before application work. Rollback-state tests bind the captured policy
to state version 3 so later policy drift cannot reinterpret a stored definition.

The delegated guard contract separately covers backup and candidate migration,
single-container success cleanup, legacy and definition-based container
rollback, and strict dedicated-key SSH behavior. The built image must contain
the manifest and bundle files with their declared modes. Real installation must
then exercise update, guard rollback/re-update, the stopped-probe definition
comparison, immutable application deployment, and private installed-browser
acceptance.

## Test layers

- Unit tests cover pure parsing, policy, state machines, and error normalization.
- Production-hardening tests cover CSRF/origin behavior, login lockout,
  secure-cookie flags, active-session renewal, inactivity-expiry preference,
  private/Tailscale/provider allowlists, metadata/public
  target rejection, correlation/security headers, readiness/admin isolation,
  queue/storage metrics, configured retention, atomic disk-full writes, empty
  artifacts, and corrupt/valid backup restore drills.
- API tests use isolated temporary databases and deterministic fake providers.
- Migration tests upgrade pre-0004/0005/0007/0008/0009/0010/0011/0012/0013/0014/0015/0016/0017 databases and prove
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
  separate conditioning-fallback and comparison-failure policies, disclosed
  unconditioned results, and verified/unverified API/browser labels.
- Provider contract tests exercise the same behavioral suite for every adapter.
- Ollama tests cover fragmented NDJSON, completion metadata, mid-stream errors,
  malformed frames, timeout/unavailable behavior, and cancellation closure.
- Turn tests cover legal transitions, atomic linked state, safe failures,
  deterministic removal of terse, bracketed, placeholder, and demonstrative
  premature media-completion claims,
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
  rejection, manual bypass disclosure, late provider bootstrap, and selected
  settings/LoRA payloads.
- Capability-planning tests prove persona reply prose cannot attach identity
  conditioning to an unrelated image, genuine persona subjects retain the hard
  semantic requirement, explicit no-persona wording overrides an incorrect
  positive model classification, configured workflows remain preferred,
  explicit fallback affects runtime, and only blocked pending plans can be replanned.
- Picture-message contract tests prove clear image actions auto-run under the
  selected persona's saved permission, retired `always_ask` input cannot restore
  image approval, story/discussion prompts create no request, a disabled persona
  suppresses only conversational image planning, direct and planned jobs return
  durable transcript attachments, reload retains lifecycle state, and
  failed/cancelled attachments create linked automatic retries.
- Media-recovery tests prove migration/startup recover only existing
  owner/chat/plan-linked generated files, fail closed for strict identity
  failures even without an attempt row, preserve `not_applicable` for ordinary
  images, preserve `unconditioned` truthfully, never chmod outside generated
  roots, and reconcile missing files across attachment, request, job, and audit
  state.
- ComfyUI workflow-inspection tests fake `/object_info` and cover detected image
  inputs, missing node classes/assets and required inputs, disconnected identity
  inputs, broken links, authentication, bounded provider metadata, and safe
  provider errors without claiming a live generation passed. Provider URL policy
  is covered separately by production-hardening tests.
- Chat-title tests cover the canonical browser placeholder, legacy placeholder
  recognition, punctuation variants, and rejection of placeholder model output
  so the title Task Model cannot restore an untitled chat after a completed turn.
- Resource-coordination tests cover real provider response parsing, unknown and
  unavailable telemetry, admin isolation, disabled/observe/managed policy,
  endpoint-fingerprint authorization, verified release, safe timeout,
  cancellation, durable content-free audit, non-blocking media admission, and
  chat-priority serialization. They also prove that unknown-demand local image
  work receives no false capacity admission, managed post-job cleanup retains
  the lease until release finishes, synchronous job waits include that
  finalizer, observe mode never releases, and work cancelled before execution
  cannot release a provider. Cancellation after execution still performs
  post-provider cleanup exactly once. Queue lifecycle tests also prove that
  concurrent teardown rejects late follow-up submissions and clears rejected
  token/execution bookkeeping rather than leaving work in a stopped queue.
  They cover the opposite interleaving too: a submission accepted immediately
  before the gate closes is durably cancelled exactly once and releases its
  coordinator ownership. A deterministic coordinator-wake test proves
  cancellation cannot make detached pending work start during teardown. Failed
  shutdown retains the old queue, blocks restart, and permits a stop retry to
  clear the failure only after that queue is idle.
  Deterministic fakes replace live GPU services in CI.
- Context tests cover multi-worker causal ordering, independent chats, explicit
  provider allocation, budget accounting, exact memory deduplication, oversized
  protected content, durable summaries, and degraded summary fallback.
- Memory tests cover legacy data migration, exact-duplicate supersession, FTS
  population/ranking, active-only scoped retrieval, nonblocking extraction,
  provenance, review transitions, superseding edits, forget/undo, extraction
  failure, secret-like candidate rejection, forget-versus-delete semantics,
  permanent history/FTS removal, atomic bulk actions, canonical routes, and owner
  isolation. They also prove that edited chat-memory proposals remain pending and
  cannot displace approved correction context before review. Chat data-action
  tests distinguish bulk hide from permanent delete.
- Vitest covers the phase machine, settings normalization, canonical API/error
  behavior, fragmented SSE parsing, protected media rendering, routing, and safe
  markdown, capability approval/denial state, Task Model settings/audits, and
  media catalog planning, GPU coordination controls/status, and canonical
  multipart identity-reference transport, and memory selection/bulk-action
  confirmation behavior. Durable attachment coverage verifies compact
  progress/errors, scoped retry/cancel, blur-off default, and
  reveal-then-preview when enabled. Shared-viewer coverage verifies avatars,
  chat images, Visual Identity references/comparisons, and picker thumbnails
  remain above the app and close by image, backdrop, button, or Escape. Visual
  Identity coverage requires a plain-language
  readiness view, visible editable generation/comparison policies,
  closed-by-default advanced diagnostics, fictional-persona
  rights wording, owner-protected thumbnail selection without opaque media ID
  entry, and accessible information-button to tooltip associations. Everyday
  settings tests keep common controls visible while provider tuning and
  credentials remain closed by default. Operator settings coverage asserts
  effective model state, runtime-effective per-model overrides, closed role and
  resource editors, independent persistence actions, safe coordination wording,
  and backup restore verification. Media Catalog coverage imports API-format
  identity workflow JSON, selects an exact binding/model, preserves targeted
  request/persona context, and exercises the active blocked-plan recheck.
  Direct-LAN client-ID coverage proves that chat does not require the
  secure-context-only `crypto.randomUUID`; Data settings coverage exercises the
  administrator backup verification action and visible restore-drill result.
  Task Model and Media Catalog settings tests must preserve unsaved edits when
  refresh responses finish late.
  Playwright runs every browser journey in both a desktop Chromium context and
  a Pixel-class touch context. It waits for and inspects mutation requests
  rather than inferring a completed save from unrelated visible state.
  Playwright deterministically
  covers onboarding/login, streamed chat, settings, memory review, and media
  while rejecting legacy API calls. It also checks computed input/select and
  native option colors in both themes so browser-default light controls cannot
  make dark-theme text unreadable. It also verifies information tips on hover
  and keyboard focus, closed advanced sections, collapsed persona editors, and
  the operator path from readiness summaries into Task Model and Media Catalog
  editors without exposing a misleading global save action.
  A direct-media journey proves that active
  work exposes an enabled cancellation control, calls the canonical job DELETE
  endpoint, returns to `idle`, and does not misreport acknowledged cancellation
  as an error.
- The human-experience scenario gate selects real API and browser tests for
  200-turn context, corrections, persona switching, memory scope, truthful media
  wording, independent follow-ups, provider degradation, durable media retry and
  reload, deterministic image fallback, completed-file Kokoro cleanup and
  interruption, blur interaction, title reconciliation, and composer access.
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
  disabled-verifier readiness, the installed current migration head, durable
  conditioned planning, a strict missing-workflow block followed by an audited
  `allow_unconditioned` replan with disclosed/unverified fields, and clean
  shutdown. Provider schema inspection is deterministic in CI; real ComfyUI
  workflow and identity-provider hardware acceptance remain separate explicit
  checks.
