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
policy, deterministic explainable plans, pre-submission stale-plan rejection,
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
immutable identity/profile/reference/workflow snapshots, pre-submission digest
and revision checks, prompt plus reference composition, stage cancellation
checks, generated-media plan provenance, and truthful conditioned-but-unverified
browser/API state. It deliberately does not auto-validate, retry, inpaint, or
correct generated results.

Step 18C added real ComfyUI source/mask workflow bindings for explicit
image-to-image, inpaint, and outpaint jobs; durable per-plan attempt records;
inline post-generation identity comparison; bounded retries; optional
image-to-image correction graphs; and truthful verified, rejected, or
unverified results. Sequential stages use the maximum planned stage estimate
for admission rather than pretending their VRAM demand is simultaneous. Live
12 GB timing/capacity tuning remains deployment acceptance work.

Protected attachment resolution is now a real typed input. Under ADR 0029 the
platform publishes the current chat's completed images to the planner as opaque
references, resolves them back to owner-scoped artifacts itself, and confirms
with the owner before an edit runs. The task model still never receives or
supplies resource identity, and Automatic1111 remains generation-only.

Step 19 added the state-changing request header and origin boundary, strict and
HTTPS-secure cookie policy, bounded login lockout, private/Tailscale/explicit
provider URL policy, redacted JSON correlation logs, request/provider/job and
queue/storage metrics, readiness, configured retention, atomic artifact writes,
and temporary-copy backup integrity/migration drills. Deterministic failure tests
cover public/metadata targets, provider outage contracts, disk-full and empty
artifacts, corrupt snapshots, restart recovery, and clean lifecycle behavior.

Every image or video generation now writes one durable journal: ordered, timed
stages covering the request, the selected plan and the coordinator's reasoning,
identity conditioning, each attempt, the provider exchange, storage, any
comparison, and the outcome. It is reached in one click from the picture itself
and exports as a single Markdown document that can be handed to another person
alongside that image. Credentials, provider addresses, and absolute server paths
are removed before anything is stored, and a journal is deleted with its media.
Recording is guarded end to end so a diagnostics failure can never cost the
operator the artifact. See ADR 0030 and `docs/media-catalog.md`.

An operator ComfyUI workflow now declares where the request goes. Prompt,
negative prompt, seed, width, and height bindings are exact node and input pairs
validated against the inline graph, and an enabled workflow with no prompt
binding is refused rather than left to render the text saved inside it. A bound
workflow executes as the whole graph, with only its declared request inputs
replaced. Import inspection reports the writable text, seed, and dimension
inputs ComfyUI proves exist, with the value currently in each so a positive
prompt input can be told from a negative one, and never offers an input already
driven by another node. Workflows saved before bindings existed keep their
previous behavior and are reported as needing binding review. See ADR 0030 and
`docs/media-catalog.md`.

Prompt construction is now per model. A `prompt_dialect` on a model resource
declares style, prefix and suffix, its own negative prompt, whether a negative
is supported at all, trigger-word placement, and a target length; a
deterministic compiler renders the request into it before submission and records
the result in the journal. The platform safety negative stays separate from the
model's negative, and a model that takes no negative prompt never implies it
carried one. Models with no configured dialect keep the previous behavior
through an explicit default rather than compiled-in text. See ADR 0030 and
`docs/media-catalog.md`.

Capability planning now sees a bounded window of this chat's earlier user
messages, so a request that refers to something already established resolves
correctly. Persona reply prose remains excluded exactly as ADR 0017 requires,
the window is bounded in count and characters, and the window that informed a
picture is recorded in its journal. ADR 0017 is amended in place.

Generation presets are now a durable record. A preset holds its base checkpoint,
optional workflow graph, LoRAs at tested weights, sampler settings, permitted
dimensions, prompt dialect, declared stages, and an operator-written routing
card. Every resource it names must already be marked compatible with its base
model, so a preset cannot describe a combination the catalog never paired.
Automatic LoRA selection survives only inside declared open slots. Enabled base
models are backfilled into presets lazily per owner, reproducing what the
coordinator did before.

Planning now selects a preset rather than assembling a combination from scored
resource tags. Hard requirements filter, then domain coverage, operator
priority, and estimated cost decide; the plan records which preset won and why,
and execution revalidates the preset revision so an edited preset produces a
retryable failure instead of a silent substitution. A preset may declare an open
workflow slot to reach a feature-capable graph such as identity conditioning.
See ADR 0030 and `docs/media-catalog.md`.

Capability planning returns a typed scene instead of prompt text. Subject,
action, setting, wardrobe, framing, lighting, camera, and mood are rendered into
the selected preset's dialect by the deterministic compiler, so prompt syntax
stays a property of the checkpoint rather than something a small local model has
to get right. The model still cannot name a provider, model, LoRA, workflow, or
generation setting. Direct requests keep the user's own words. The scene and the
compiled text are both recorded in the journal. See ADR 0030.

Requests now route to a preset. The platform hard-filters what is legal and
offers the Task Model a bounded shortlist of opaque labels carrying each
preset's title and the operator's routing card; the model returns a label or
nothing. Resource identity never reaches it. The full hard filter still runs at
plan time and can reject the model's choice with a visible warning, and
selection falls back to the deterministic score whenever the model expresses no
preference, fails, or times out. The plan records the winner, whether the model
or the score chose it, and what else was considered. See ADR 0030.

A routing tester sits under advanced disclosure in Media Catalog. It runs the
real shortlist, task model, and planner for a pasted message and reports what
was offered, what was chosen, and by whom, without generating anything. It is
labeled as a diagnostic expected to be removed once routing is stable.

Preset bundles are the serialized format, and the built-in starters ship through
it. A bundle names assets by the filename the provider reports rather than by
local resource IDs, so the same artifact works for starters now and sharing
later. Starters carry published per-family defaults and are labeled as a
starting point rather than a measurement; a starter whose model file is not in
the catalog is reported by name instead of installed, and one whose name already
exists is skipped rather than overwritten. See ADR 0030.

Presets can declare stages. A multi-pass preset runs each pass in order with the
previous picture as its source, so an identity pass or a detail pass is part of
the recipe rather than a correction that only runs after a failed comparison.
Later stages must declare a source image binding and are refused at save time
otherwise, each stage journals separately, intermediates never reach the
library, and sequential stages are admitted as the largest rather than the sum,
per ADR 0013. See ADR 0030.

Persona resemblance is now structural. A profile records the conditioning
mechanism that produces it alongside its reviewed references and appearance
text, and the comparison-driven retry loop is off unless an operator switches it
on. Comparison is advisory measurement: nothing requires a verifier to be
running, readiness is answered on demand rather than polled, and a persona image
generates and is labeled `unverified` when none is configured. `verified` still
requires a real passed comparison. Migration `0024_identity_spec` adds the spec
columns and switches the retry loop off for existing profiles without touching
references, consent, validations, or completed plans. See ADR 0031.

Presets now declare which identity mechanisms they implement, and a persona
image is planned only against a preset whose mechanism the persona's Identity
Spec requires. A preset that cannot honor the spec is rejected with a reason
naming the mechanism instead of silently producing an unconditioned picture. The
ADR 0018 unconditioned fallback is unchanged and drops the mechanism requirement
along with the feature it belonged to. See ADR 0031.

Generated pictures are retained with the scene that produced them, and a later
request that matches one closely enough is served instead of generated, which
the journal records. Matching is over scene fields rather than prompt text, the
subject dominates, and a picture is never served twice into one conversation nor
recycled into the conversation that made it. Pictures can be added by hand with
a description. The library is capped by `MEDIA_LIBRARY_ENTRY_LIMIT` and retires
rather than deletes. See ADR 0030.

Media Catalog is preset-first. Presets lead the screen with named fields for
prompt style, prefix and suffix, negative-prompt support, trigger placement,
sampling, and dimensions; the individual models, LoRAs, and workflows sit behind
an Inventory disclosure. The raw definition remains available but is no longer
the only way to edit a recipe. See ADR 0030 and `docs/settings-experience.md`.

Visual Identity became Persona Pictures: one surface holding a persona's
appearance, its reviewed references, and the pictures kept for reuse, with no
increase in top-level settings tabs. Kept pictures show the description they are
matched against, and forgetting one stops reuse without deleting the image. See
`docs/settings-experience.md`.

A persona records which presets are known to work for it, best first, editable
on Persona Pictures. Routing consults that preference after the task model's own
choice and only among presets that already passed the hard filter; a stale
preference is skipped rather than blocking. The plan explanation names which of
the three chose. Migration `0026_persona_preset_preferences` adds the column
empty, because an existing persona expresses no preference. See ADR 0030.

A persona has a durable scene backlog: pictures proposed but not made, each
carrying where the idea came from. Operators move entries between proposed,
approved, and retired; states describing work are not offered as something to
click. Nothing generates from it yet, and the automatic proposal of scenes from
a persona's card, lorebook, and conversation themes is separate work. See
ADR 0030.

Scenes can now be proposed automatically from a persona's card, lorebook, and
recent conversation themes by a dedicated Task Model role. Each proposal records
which source suggested it and the detail it drew on, arrives as `proposed`, and
is never auto-approved. The response says whether the model actually answered.
Migration `0028_scene_proposal_role` widens the role vocabulary by rebuilding the
profile and run tables, copying existing rows verbatim. See ADR 0030.

## Deferred voice core

10. Blind TTS evaluation and provider decision.
11. Streaming, provider-neutral TTS v2.
12. Hybrid STT and turn detection.
13. Natural turn-taking and barge-in.

These steps remain valid, but TTS provider replacement is deferred while the
working Kokoro path remains available. They are not prerequisites for the media
and platform foundation below. Once the current human-experience restoration is
accepted in production, these voice steps are the next capability expansion;
additional catalog breadth does not take priority over them.

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

21. **Approachable settings experience — delivered.** Visual Identity provides
    guided reference setup, truthful generation-versus-verification readiness,
    editable missing-conditioning and comparison-failure behavior, and protected
    thumbnail pickers. Media Catalog adds targeted identity workflow
    import/inspection/binding and blocked-plan rechecks without claiming a schema
    check is a live provider success. Everyday tabs lead with common controls,
    accessible information tips, and closed expert details. Models, Task Models,
    Media Catalog, GPU Coordination, and Data now lead with effective-state and
    readiness summaries, use collapsed named editors, keep warnings visible,
    and separate independent save and destructive actions. The settings shell
    delegates those workflows to focused typed modules. See
    `docs/settings-experience.md`.
    The provider-bootstrap repair canonicalizes legacy local image aliases and
    seeds a missing starter catalog model when media is enabled after initial
    setup, while preserving operator-managed resources under ADR 0016.
    Persona-subject planning now keeps user intent authoritative and makes
    identity workflow blocks actionable under ADR 0017. ADR 0018 restores
    supporting image generation through an explicit warned fallback while
    retaining strict conditioning as a user-selectable policy.

22. **Human picture-message delivery — implementation published; installed
    acceptance pending.** Ordinary explicit image
    requests auto-run under a saved policy, while deterministic negative-intent
    gating prevents story/discussion jobs. Direct and planned work share durable
    transcript attachments, reload recovery, compact cancel/retry/error UI,
    protected content, identity labels, optional collapsed details, and a
    persisted blur control defaulted off. Multiple configured catalog backends
    are readiness-filtered before deterministic selection. See ADRs 0019–0020.

23. **Human conversation cleanup — implementation published; installed
    acceptance pending.** Persona replies commit before
    independent title, capability, and memory follow-ups. Clear image requests
    buffer persona prose through a durable-evidence claim guard. The chat memory
    action creates an editable pending fact instead of promoting raw assistant
    text, default chat chrome uses progressive disclosure, and visible Cancel
    actions are scoped to work that can still be canceled. Deterministic scenario
    evaluations cover long conversations, corrections, persona switching, memory
    boundaries, media/provider degradation, and completed-file Kokoro behavior.
    See ADR 0021.

24. **Self-maintaining restricted deployment guard — implementation
    complete, live migration pending.** A permanent forced-command launcher now
    selects immutable guard bundles, accepts updates only from the exact running
    application digest, validates candidate payload/configuration independently,
    and activates atomically. The existing private deployment still needs its
    one-time supervised migration followed by remote update, guard
    rollback/re-update, one-container deployment, and browser acceptance. See
    ADR 0025.

25. **Persona character card — delivered.** Personas carry authored definition,
    personality, style, and behavior material that is always present in the
    protected prompt section. Because that section fails a turn rather than
    degrading, the card is capped when it is saved, against the narrowest context
    window the account has configured, and the rejection names the estimate, cap,
    prompt budget, and window. The editor prices each field as it is typed. It
    deliberately does not add example dialogue or lorebooks: those are separate
    budgeted sections in `docs/persona-depth-spec.md` and remain gated by the
    unresolved 8k-context-versus-VRAM question. See ADR 0026.

26. **Conversation history floor and persona example dialogue — delivered.**
    Conversation history now keeps a reserved share of the prompt budget. When the
    assembled prompt would leave less, optional sections yield in reverse authority
    order — summary, saved memory, then example dialogue — and the turn reports what
    it dropped instead of claiming to have used it. Nothing yields on a turn with no
    history, and protected material never yields. On top of that floor, personas
    carry `<START>`-delimited example exchanges that show how they speak, included
    whole or not at all under a 10 percent allowance and never reaching the
    summary, memory, or capability roles. It deliberately does not add lorebooks.
    See ADR 0027.

27. **Persona lorebooks — delivered.** Background detail is injected only when the
    current message or the last three transcript messages mention one of an entry's
    literal keywords. Matching is platform-owned and deterministic; keys are never
    treated as patterns, injected lore is never rescanned, and fired entries are
    included whole in priority order under a 12 percent allowance that yields ahead
    of example dialogue. An owner-scoped preview route reports which entries a
    pasted message fires and which of them fit. This completes
    `docs/persona-depth-spec.md`. It deliberately does not add semantic retrieval
    or workspace-shared entries. See ADR 0028.

Steps are delivered and reviewed independently. Step 11 cannot select providers
until a future listening decision is approved. Any future deployment acceptance
must use the intended LAN hardware and service topology.
