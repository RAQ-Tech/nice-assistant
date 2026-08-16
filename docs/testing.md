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
server exclusion remains. Static analysis also enforces a cyclomatic complexity
ceiling of 15. Thirteen functions predate the rule and carry an explicit
`# noqa: C901`; those markers are the debt list, and new code is expected to stay
under the ceiling rather than add to it.

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

Bundle version 3 observability tests execute both `inspect` and `health` and
require an integer `guard_bundle_version` equal to the active manifest plus a
boolean `preserve_explicit_mac` equal to the persisted root-only policy. They
use both `false` and `true` policy fixtures and require exact JSON types. An
invalid or insecure manifest must fail closed instead of producing a guessed
version. Live compatibility acceptance keeps version 2 operational with its
legacy response shape, then exercises version 2 to version 3 update, guard
rollback to version 2, and re-update to version 3. The two fields must appear
under version 3 and later and must return with the same policy after the
re-update.

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
review asserts the exact current version, version 3 for the observability
release, and final LF-byte hashes for the guard and both jq filters. Launcher
tests additionally select version 1, prove application deploy/rollback are
refused while inspection remains available, and reselect version 2 before
application work. Rollback-state tests bind the captured policy to state
version 3 so later policy drift cannot reinterpret a stored definition.

The delegated guard contract separately covers backup and candidate migration,
single-container success cleanup, legacy and definition-based container
rollback, and strict dedicated-key SSH behavior. The built image must contain
the manifest and bundle files with their declared modes. The correction
rollout's real installation must then exercise the stopped-probe definition
comparison, immutable application deployment of the image that supplies bundle
version 3, the version 2 to version 3 guard update, version 3 observability,
guard rollback to version 2, re-update with observability restored, and private
installed-browser acceptance.

## Test layers

- Unit tests cover pure parsing, policy, state machines, and error normalization.
- Production-hardening tests cover CSRF/origin behavior, login lockout,
  secure-cookie flags, active-session renewal, inactivity-expiry preference,
  private/Tailscale/provider allowlists, metadata/public
  target rejection, correlation/security headers, readiness/admin isolation,
  queue/storage metrics, configured retention, atomic disk-full writes, empty
  artifacts, and corrupt/valid backup restore drills.
- Media preference tests prove every value the settings endpoint accepts survives
  the runtime normalizer unchanged, that an unusable provider, backend, size,
  quality, video model, video size, or duration is refused with a message naming
  the accepted values, that a refused save changes nothing, that legacy provider
  aliases still save in canonical form, and that an unchanged stored value does
  not block saving unrelated settings.
- Generation journal tests prove redaction removes credentials, provider
  addresses, and absolute paths before anything is stored; that oversized and
  deeply nested details are bounded rather than dropped whole; that one
  generation writes exactly one journal reachable by media ID; that the export
  is a named Markdown document which passes `scripts/audit_public_repo.py`; that
  journals are owner-scoped; that a journal is deleted with its media; and that
  a failing journal still returns the picture. Browser tests cover reaching the
  log in one click from the image, the download link, and the honest message
  when no log was recorded.
- Workflow binding tests prove a graph using arbitrary node IDs receives the
  request prompt, negative prompt, seed, and dimensions through declared
  bindings; that such a graph runs whole rather than merged over the built-in
  one; that a binding naming a missing node fails loudly; that a workflow saved
  before bindings existed keeps its previous behavior; that an enabled workflow
  without a prompt binding is refused; and that inspection lists text, seed, and
  dimension candidates while never offering an input driven by another node.
- Prompt dialect tests prove two dialects render the same request differently,
  that a dialect declaring no negative support sends none and does not imply the
  safety negative was carried, that the platform safety negative stays separate
  from the model's own, that compilation is pure, that trigger words land where
  the dialect says, that a target length truncates on a tag boundary, that an
  unstated dialect reproduces the previous behavior, that invalid dialect fields
  are refused by name, that the client sends compiled text without restyling it,
  and that the compiled positive and negative reach the journal.
- Planning context tests prove earlier user messages reach planning so a
  reference resolves, that persona reply prose still never appears in a planning
  payload, that the window is bounded in count and characters with the newest
  messages retained, that a first message plans with a one-entry window, and that
  the window which informed a picture is recorded in its journal.
- Generation preset tests prove a definition needs a base model and refuses
  unsupported fields, malformed dimensions, duplicate LoRAs, and bad slot names;
  that a preset with no declared stages is single-pass; that every enabled model
  is backfilled once into a preset reproducing its dialect, sampler, size, and a
  single open LoRA slot; that create, update, and delete round-trip with a
  revision bump; that a preset cannot name a LoRA the catalog never paired with
  its base model; that names are unique per owner; and that presets are
  owner-scoped. Planning tests prove the chosen preset and its reason appear in
  the plan explanation, that automatic LoRA selection fills a declared open slot
  and adds nothing to a preset without one, and that editing a preset after
  planning produces a retryable conflict rather than a substitution.
- Scene contract tests prove every field is present, trimmed, and bounded; that
  rendering follows the declared field order and leaves no stray separators; that
  the summary stays a short human line; that the schema asks for a scene and not
  for prompt text; that an entirely empty scene is refused; that an injected
  model, workflow, or setting field is still rejected; that a scene is rendered
  into the dialect rather than the summary while a direct request keeps the
  user's own words; and that the scene which produced a picture is recorded with
  it.
- Preset routing tests prove the shortlist carries labels and routing cards but
  no resource identity, that the schema offers only labels the platform
  published plus an empty sentinel, that a label which was never offered is
  rejected, that no preset field appears when there is no shortlist, that the
  model can route to a preset the deterministic score would not have picked,
  that an absent choice or a failing task model falls back to that score, and
  that the plan records what was considered.
- Routing tester tests prove the reported shortlist carries titles and routing
  cards but no resource identity, that the chosen preset and who chose it are
  reported, that a message needing no image says so, that a task model which
  fell back is reported rather than shown as "no image wanted", that an empty
  message is refused, and that the preview is owner-scoped. Browser tests cover
  the reported shortlist, the winner and who chose it, the no-routing-card
  notice, a surfaced task-model failure, and the removal label.
- Preset bundle tests prove a bundle needs a supported version and at least one
  preset, that every entry names the model file it expects, that a malformed
  definition or unsupported field is refused before anything is installed, that
  the shipped starters cover distinct dialects and declare a no-negative model
  honestly, that every starter states it is untested here, that a starter whose
  model is absent is named rather than installed, that one installs once its
  model is cataloged, that a second install never overwrites an edited preset,
  and that starters are owner-scoped. Browser tests cover the starting-point
  labelling, the named missing model, the installed and skipped report, and an
  existing preset shown as kept.
- Preset stage tests prove a two-stage preset submits both passes with the
  second receiving the first's picture as an editing operation, that each stage
  records its own journal entry, that only the final pass reaches the library
  and no scratch file is left behind, that a later stage without a source image
  binding is refused when the preset is saved, and that sequential stages are
  costed as the largest rather than the sum.
- Identity spec tests prove a new profile records its conditioning mechanism and
  leaves the comparison retry loop off, that an unimplemented mechanism is
  refused, that an operator can switch the bounded retry on deliberately, that a
  persona image generates and completes with no verifier configured, and that no
  code path polls the verifier on a timer.
- Identity mechanism tests prove a preset may declare the mechanisms it
  implements, that declaring nothing means it implements nothing, that an
  unknown mechanism is refused by name, that a preset derived from an existing
  model still declares reference conditioning, and that planning rejects a
  preset which cannot honor the persona's spec with a reason naming the
  mechanism.
- Picture library tests prove a different subject never matches however similar
  the rest, that the same scene matches strongly, that a request asking for more
  than the stored picture scores lower, that an empty scene matches nothing,
  that a generated picture is retained with its scene, that a matching request
  in another conversation is served with no provider call and journals that it
  was, that a picture is never recycled into the conversation that made it, that
  a different subject generates instead, that hand-added pictures need a
  description, and that the cap retires rather than deletes.
- Preset editor tests prove the values that decide how a picture comes out are
  named fields rather than raw JSON, that a model taking no negative prompt says
  so along with the safety-negative consequence, that a preset with no routing
  card is flagged, that saving is offered only after a change, and that an empty
  list explains where presets come from. Browser journeys open the demoted
  Inventory disclosure before reaching a raw resource editor.
- Kept picture tests prove an entry shows the description it will be matched
  against, that a retired entry explains the keep limit without implying
  deletion, that forgetting an entry says the picture itself stays, and that an
  empty library says so rather than rendering nothing. The browser journey
  covers the merged Persona Pictures surface.
- Persona preset preference tests prove a persona records an ordered preference
  and expresses none until one is set, that a preference wins over the
  deterministic score and says so in the plan, that the task model's own choice
  still outranks it, that a preference naming an unusable preset is skipped
  rather than blocking, and that the list is bounded. Browser tests cover adding,
  reordering, and removing a preference, and that removal does not claim to
  touch the preset.
- Scene backlog tests prove a proposal records what it is and where it came
  from with nothing made from it, that a proposal needs a description and a
  persona the owner has, that an operator can approve and retire but cannot set
  a state that claims work, that a retired scene returns to proposed rather than
  jumping to approved, that entries filter by persona and state, that they are
  owner-scoped, and that one can be deleted outright.
- Scene proposal tests prove the model is asked for a scene and its provenance,
  that the prompt forbids naming resources and supplies what was already
  proposed, that an empty scene or unknown source is refused, that duplicate
  ideas collapse and the limit holds, that proposals land in the backlog as
  `proposed` with their provenance, that the persona card and existing ideas
  reach the model, that a model which did not answer is reported rather than
  looking like no ideas, and that a persona the owner does not have is refused.
- Pre-generation policy tests prove a quiet window may wrap past midnight, that
  an empty window never matches, that production is off unless switched on, that
  a waiting conversation and an already-queued picture both outrank it, that a
  refusal names the window or the missing approval, and that quiet-idle-approved
  is the only way through. Readiness tests prove the reason is reported rather
  than silence, and that only approved scenes are counted.
- Documented-claim tests assert absences that would otherwise regress silently:
  resource audit rows carry no endpoint URL, credential, or generated content, and
  persona card, example dialogue, and lore reach the persona prompt without reaching
  the summary, memory-extraction, or capability roles or the durable transcript.
- Owner profile tests cover empty-profile neutrality, the display name reaching the
  prompt, the labelled section, the cap at 4096 and 8192, refusal of an oversized
  profile leaving nothing stored, a raised allocation accepting a previously refused
  profile, and the profile never reaching platform task roles.
- Memory retention tests prove expiry is off unless configured, that rejected and
  forgotten rows past the window are removed, that active, pending, superseded, and
  recently discarded rows are never touched, and that the configured window is
  reported.
- Turn reconnect tests prove a mid-reply reconnect renders the reply exactly once,
  that deltas produced after the snapshot still arrive, that a cursor pointing into
  evicted events leaves no hole, and that a fresh subscriber sees the reply once.
- Second task adapter tests prove only schema-capable models are advertised, that
  health makes no untested reachability claim, that a missing account key fails
  before any request, that the role schema is sent as a strict structured-output
  envelope, that its output satisfies the same parser Ollama output does, that a
  refusal is terminal rather than malformed, that an unexpected body is not leaked,
  and that the adapter is not offered for conversation.
- Background production tests prove an approved scene is made and recorded
  against its entry, that it is retained and journalled like any other picture,
  that a proposed scene is never made, that the per-run limit holds, that the
  window and the off switch each refuse with their own reason, that the job is
  queued as bulk, that a conversation completes while a background picture holds
  the media lane, and that a failure or a restart returns the scene to
  `approved` rather than stranding it in `generating`.
- Chat binding tests reproduce both defects before refusing them: saving a
  persona from another workspace onto a chat, and a turn payload naming a
  different persona or workspace. Each proves the refusal happens before
  anything durable is written, that repeating the bound values still works, and
  that title, model, and memory mode remain editable. Repair tests upgrade a
  database from the revision before the migration, because a database already at
  head would not run the migration under test.
- Task model credential tests prove a keyless OpenAI profile is not ready, that
  the stated reason is the missing key rather than a missing adapter, that a
  blank key counts as none, that the key never appears in a response, that
  readiness never claims a live request was made, that a keyless primary reports
  `fallback_ready` only when the fallback genuinely is ready, and that a
  provider needing no credential is unaffected.
- Turn pipeline tests pin what the extraction newly guarantees: that the values
  a turn resolved cannot be rewritten by its own follow-ups, that two turns do
  not share mutable defaults, and that the application instructions sent to the
  model follow from what was actually offered and in which order.
- Photo set tests prove a frame may change pose but not wardrobe, that a
  disallowed field is dropped rather than the frame refused, that seeds follow
  from the base rather than being random, that one frame is not a set and
  neither is a set whose frames are identical, that every frame is generated
  rather than served from the library, that each frame links back to its set
  with its own seed, that the journal records set and frame, and that a partly
  made set reports `partial` rather than `done`.
- Photo set serving tests prove a matching request arrives as several frames
  without generating anything, that the number is bounded when more frames
  exist, that a conversation never receives the same frame twice across two
  requests, that a partly made set is still served from, and that an ordinary
  picture still arrives alone.
- The formatter check covers `app`, `migrations`, `tests`, and `scripts` in
  full. It used to cover a curated list of modules, which meant editing anything
  outside that list failed at the last gate of the verifier rather than the
  first. The list was four files short of the whole repository, so the list was
  costing more than it saved.
- Preset signal tests prove generating a picture counts for nothing, that
  sending one again and removing one are counted against the preset that made
  it, that a preset used often and removed often is not promoted, that more
  evidence breaks a tie, that the summary never claims to have learned
  anything, that counts are individually resettable, and that they are
  owner-scoped.
- Preset export tests prove resources leave as names and never as local
  identifiers, that a workflow and an identity mechanism are named as
  requirements rather than dropped, that every pass of a multi-pass preset is
  named, that an asset which cannot be named is reported, that nothing measured
  on this machine appears anywhere in the file, that the preview lists the
  fields that will leave, and that an export round-trips through the importer's
  own validation.
- Preset import tests prove a recipe whose model is installed here imports, that
  one naming an absent model is refused by name and changes nothing, that a file
  is all or nothing when only some of it could install, that a workflow slot is
  declared as running somebody else's graph, that every import says it was
  tested elsewhere, that requirements travel into the notes, that an existing
  name is never overwritten, that a foreign VRAM figure is dropped rather than
  refused, and that an exported preset imports into another account.
- API tests use isolated temporary databases and deterministic fake providers.
- Migration tests upgrade pre-0004/0005/0007/0008/0009/0010/0011/0012/0013/0014/0015/0016/0017/0019/0021/0022 databases and prove
  chats, messages, jobs, media, memories, turn ordering, stored artifact links,
  Task Model profiles, and imported catalog resources survive.
- Persona character card tests cover render order and empty-card neutrality, the
  budget arithmetic at 4096 and 8192, the narrowest-configured-window rule, the
  422 message naming estimate/cap/budget/window, an over-limit save leaving the
  stored card unchanged, owner isolation, refusal of card fields on the general
  persona route, and a card saved at the cap planning and reaching the provider
  without `context_too_large`. Browser tests cover live per-field counts, the
  budget meter and its warning state, saving through the card route, and the
  rejection message surfacing. Both suites price one shared card, so a change to
  either side's labels or estimator fails a test instead of showing the operator
  a number the platform will not honour.
- Lorebook tests cover generated plural forms and the vowel-before-y and phrase
  exceptions, per-entry opt-out, which authored keys a preview reports as fired,
  word-boundary matching, case sensitivity, literal handling
  of pattern-looking keys, punctuation keys, secondary keys as an additional
  requirement, `always_on` without keys, the bounded scan window, an entry falling
  out of it, injected lore not triggering further entries, priority and recency
  ordering, whole-entry inclusion with skipped entries not blocking smaller ones,
  key parsing bounds, CRUD and validation, preview contents, disabled entries
  excluded from preview and turns, cross-persona entry access, owner isolation on
  every route, and a fired entry reaching the provider while a quiet one does not.
  Browser tests cover deferred loading, firing summaries, always-on and disabled
  labelling, keyword list editing, preview results including what did not fit,
  rejection surfacing, and deletion.
- History floor tests cover no yielding when the conversation already fits,
  reverse-authority yielding, yielding only as far as the floor requires, a first
  turn with no history keeping its context, and protected sections surviving.
  Example dialogue tests cover block splitting, placeholder substitution, whole
  exchanges included or omitted rather than truncated, substituted text reaching
  the provider, its absence from platform task prompts, and no example section for
  a persona without one.
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
  strict explicit-image classification with ordinary-text negative cases, one
  platform-owned pending-image acknowledgement, removal of complete, unclosed,
  nested, and chunk-split protected prompt envelopes before streaming/persistence, safe
  legacy-message and summary reads, exclusion of removed text from future
  context and summary reuse,
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
  failed/cancelled attachments create linked automatic retries. They also prove
  an explicit image request receives a deterministic durable request when the
  Task Model returns no request, and receives a failed attachment rather than a
  silent promise when no image provider is ready.
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
  post-provider cleanup exactly once. A completed managed-to-disabled policy
  change prevents stale authorized provider release and timeout paths and
  immediately admits previously gated work. Queue lifecycle tests also prove that concurrent
  teardown rejects late follow-up submissions and clears rejected token/execution
  bookkeeping rather than leaving work in a stopped queue.
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
