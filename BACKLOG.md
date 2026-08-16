# Backlog

The single list of remaining work. Each entry says what is left, why it
matters, and what is holding it up. Detail lives in the linked documents; this
file is the index and the honest status.

Ordering inside a section is rough priority, highest first. Sections are
ordered by what can actually be started today.

## Definition of done

An item is complete when its behavior is implemented, its documentation is
updated in the same change, and the repository verifier passes with no errors:

```
npm run verify
```

That command runs, in order: browser typecheck, browser unit tests, browser
production build, the public-repository privacy audit, Python compile, static
analysis and formatter checks, the unit/API suite with coverage, the process
smoke check, the Playwright browser journeys, and the human-experience
scenarios. Use `npm run verify:foundation` to repeat the suite three times when
a change could introduce order-dependent or flaky behavior.

Items marked **Blocked - deployment** additionally require the installed
browser journey on the real private-LAN topology. A service test or a mocked
browser route is not acceptance evidence for those.

Most items carry their own **Done when** list. Those criteria are in addition to
the verifier, never instead of it.

Last full verifier run: passed, 2026-08-15.

## Status vocabulary

- **Ready** - no external dependency; can be started in this repository now.
- **Needs decision** - implementable, but requires an owner policy choice first.
- **Blocked - operator** - requires an approved evaluation or provider choice.
- **Blocked - deployment** - requires the installed private-LAN deployment.
- **Not advertised** - deliberately absent; listed so it is never mistaken for
  a regression or quietly shipped as a stub.

---

## 1. Ready

### 1A. Image generation program

Owner-directed, decided 2026-08-14. Design and rationale are in
[ADR 0030](docs/decisions/0030-preset-directed-image-generation.md) and
[ADR 0031](docs/decisions/0031-structural-identity-conditioning.md).

Phase 1 is delivered: the per-generation journal, declared workflow request
bindings, per-model prompt dialects, and conversation context for planning.
Background production of approved scenes is delivered too, so the quiet-hours
policy now has something consuming it. The
generation preset record, its backfill, preset-directed planning, the scene
contract, shortlist routing, the routing tester, starter presets,
multi-pass stages, structural persona identity, the retained picture library,
a preset-first Media Catalog, the merged Persona Pictures surface, per-persona
preset preferences, and the scene backlog with automatic proposals are
delivered too. See
`docs/media-catalog.md`. Every item below adds its own stages to the journal
rather than replacing it, so each one is reviewable from the picture it
produced.

**Phase 1 is complete**, and so is the scene record that Phase 3 depends on.
Items may be reordered if one is blocked.

This program does not displace the voice-core items in section 3. Those remain
the highest product priority and are blocked on an operator decision that has
not been made; this work is what can actually progress in the meantime.

1. **Generate a photo set as one unit.** One idea, several frames sharing
    wardrobe, room, lighting, and seed family, varying pose and angle. Source:
    ADR 0030.

    Done when:
    - A set record exists with its shared scene and its per-frame variations,
      and each frame links to the set that produced it.
    - The frames of a set are generated from one plan, so wardrobe, room, and
      lighting cannot drift between them, and the seed relationship between
      frames is recorded rather than incidental.
    - A set is produced as bulk work on the media lane, like any other picture
      nobody is waiting for.
    - A partly generated set is honest about it: finished frames are usable and
      the set says how many are missing, rather than the set appearing complete.
    - The journal shows the set, its shared decisions, and each frame.

2. **Send several frames of one set into a conversation.** A set is only worth
    generating if it can arrive as a set. Source: ADR 0030.

    Done when:
    - Serving can answer one request with more than one frame from the same set,
      and the frames chosen are the ones that were not already sent.
    - A conversation never receives the same frame twice, on the same rule the
      single-picture library already uses.
    - A set that is only partly generated can still be served from.
    - The number of frames sent at once is bounded and stated, not unbounded.

3. **Record which presets a person actually keeps.** Deliberately simple and
    inspectable. Source: ADR 0030.

    Done when:
    - Only explicit signals are recorded. A picture kept, sent again, or removed
      is a signal; a picture merely generated is not.
    - The weights are visible and individually resettable in settings, showing
      the count behind each one rather than an opaque score.
    - Weighting only ever reorders presets the hard filter already accepted, so
      no preference can select something incompatible.
    - Nothing in the product describes this as learning beyond what it
      measurably does.

4. **Export a preset as a file.** Source: ADR 0030.

    Done when:
    - Export produces the bundle format that already exists, scrubbed of
      machine-specific values: no base URLs, no local paths, no VRAM estimates
      measured on this machine.
    - The export preview shows exactly what will leave, field by field, before
      the file is written.
    - A preset naming resources that cannot be described portably exports with
      those named as requirements rather than silently dropped.

5. **Import a preset from a file.** Source: ADR 0030.

    Done when:
    - Import remaps referenced checkpoints and LoRAs against what is installed
      here, and states plainly which requirements it could not satisfy.
    - Imported VRAM estimates are cleared rather than trusted.
    - Import states, before it runs anything, that a workflow executes another
      person's graph on this machine.
    - An import that cannot be satisfied leaves the catalog unchanged.
    - No discovery, ratings, or registry. This is a file an operator moves
      deliberately.

6. **Choose the retained picture that matches what the persona said.** A
    persona writes its reply before anything decides which picture is attached,
    so it can say one thing while a picture of something else arrives beside it.
    Planning the picture first was rejected on 2026-08-16 because it would add a
    Task Model call to every turn, and real-time voice is the direction. This
    fixes the same problem from the other end and costs nothing per turn:
    capability planning already runs after the reply commits, so the reply is
    already there to be read. Source: ADR 0017, ADR 0021, ADR 0030.

    Done when:
    - The persona's reply may influence which already-retained picture is
      chosen, and nothing else. It may not introduce a subject, widen one,
      cause a generation, or make a picture eligible that the user's own words
      did not already make eligible. ADR 0017 holds; this ranks candidates that
      already passed it.
    - A new ADR records that narrowing, because it reads as a contradiction of
      ADR 0017 otherwise, and the reason the two are different is the whole
      argument.
    - `test_persona_reply_prose_still_never_reaches_planning` is kept, or
      replaced by a stricter test that proves prose cannot create or widen work
      while still allowing it to rank.
    - A test shows the beach-photo case fixed: a persona that describes walking
      the dog does not get a beach picture when a dog picture is retained.
    - When no retained picture matches the reply, behaviour is exactly what it
      is today. This never makes a picture arrive that would not have.

### 1B. Homepage and everyday visibility

Owner-requested 2026-08-16. Loading the browser drops straight into the last
chat, so there is nowhere to see what the assistant is currently set up to do.
The pre-generation schedule is the sharp example: it spends GPU time overnight
and is currently only visible to someone who goes looking for it in settings.

Item 7 is a prerequisite for item 11 and should be done first. The rest can be
taken in any order.

7. **Make pre-generation an owner setting, not deployment configuration.** The
    policy is read from `PREGENERATION_*` environment variables at startup, so
    it cannot be changed from the browser at all. A toggle on a dashboard would
    be a control that changes nothing, which this repository does not ship.
    Source: `app/pregeneration.py`, `app/runtime.py`, `docs/operations.md`.

    Done when:
    - The policy is stored per owner and the production runner reads the stored
      value, not the environment, on every pass.
    - The environment variables set the initial value for a new account. One
      exception, decided rather than asked: `PREGENERATION_ENABLED=0` remains a
      deployment-level refusal the browser cannot override, because this feature
      runs the GPU unattended and the machine has overheated before. When a
      deployment forbids it the control is shown disabled with that reason,
      never shown as available and then ignored. `docs/operations.md` says so.
    - A test proves that saving the setting changes what the runner does on its
      next pass, rather than only what the API returns.
    - An invalid window is refused when saved, not silently corrected later.

8. **Open on a homepage instead of the last chat.** `#/` already parses as a
    home route, but `applyCurrentRoute` immediately opens the first chat and
    rewrites the URL, so the route has never been reachable. Source:
    `frontend/src/routing.ts`, `frontend/src/app.ts`.

    Done when:
    - Loading or reloading the browser with no chat in the URL shows the
      homepage and stays there.
    - A link or reload directly to `#/chats/{id}` still opens that chat, and
      browser back and forward move between homepage and chat correctly.
    - The homepage is its own module. `app.ts` is at 646 lines against a 650
      line guard, so this cannot be added to it.
    - Existing browser journeys that assumed a chat opens on load are updated
      rather than deleted.

9. **Put the logo in the chat header as the way back.** Source: owner request.

    Done when:
    - The mark already in `web/favicon.svg` is reused. No new brand is invented
      and no external asset is fetched.
    - It is a link to the homepage, reachable by keyboard, with an accessible
      name that says where it goes.
    - It is visible and tappable at mobile width without crowding the header
      controls that are already there.
    - A browser journey clicks it from a chat and lands on the homepage.

10. **Show what is true right now on the homepage.** Information only; every
    value read from an API that already exists. Source: owner request.

    Done when:
    - It shows the persona and workspace a new chat would use, provider
      readiness for chat and images, what the job queue is doing, and the most
      recent pictures with the outcome of the last generation.
    - Nothing is modeled, estimated, or filled in with a plausible default. A
      value the platform does not have is absent, and says why.
    - Every empty state says what to do next rather than showing a blank panel.
    - It does not poll aggressively: one load, and refresh on the events the
      browser already receives.

11. **Put the pre-generation toggle and schedule on the homepage.** The reason
    this belongs on the front page rather than in settings is that it spends
    real electricity on a schedule, and a setting nobody sees is a setting
    nobody revisits. Needs item 7. Source: owner request, ADR 0030.

    Done when:
    - The switch and the quiet window are editable from the homepage, alongside
      speech on/off and memory mode, which the owner named as the other two
      worth seeing without opening settings.
    - Its current state is legible without opening anything: on or off, the
      window, whether the current hour is inside it, and what production last
      did or last refused to do and why.
    - The homepage and the settings page operate the same stored setting. Two
      controls that drift apart is the failure mode being avoided.
    - Switching it off stops the next pass, proven by a test rather than by
      the control appearing to move.

### 1C. Correctness work carried in from main

Recorded by a parallel session and verified against `main` at `0df1d89` on
2026-08-14. The reproductions are regression-test inputs, not review notes.
These come before the remaining foundation work below because each one is a
defect in behavior that already ships.

Chat workspace and persona bindings are now immutable; see ADR 0032 and
migration `0031`.

12. **Make Task Model readiness credential-aware and truthful.**

    **Observed failure:** an OpenAI Task Model profile can be saved through the API
    without an account API key, yet readiness reports `ready: true`. Execution then
    fails with `openai_api_key_missing`. The settings UI currently offers only Ollama,
    so adapter presence, account configuration, UI support, and runtime readiness are
    being conflated.

    **Required work:**

    - Pass the account/user context into provider-attempt readiness. For OpenAI, a
      missing or blank account API key must make the attempt unavailable with a safe,
      actionable message; never echo the key.
    - Separate "adapter is installed", "credentials are configured", and "a live
      request has been verified" in naming/status text. `health()` must not say
      "Configured" when it has no account credential evidence.
    - Preserve fallback semantics: a keyless OpenAI primary may report
      `fallback_ready` only when the configured fallback really is ready.
    - Do not expand OpenAI into the Task Model settings UI as part of this fix. The
      product/privacy decision in Open question 5 determines whether a later slice
      exposes it or keeps it as a contract adapter only.
    - Update task-model, settings, security, testing, and debt documentation so an
      installed adapter is not advertised as a usable provider.

    **Done when:**

    - A saved keyless OpenAI profile returns `ready: false` (or genuine
      `fallback_ready`), and an actual run fails/falls back with the same reason.
    - Blank and redacted-key cases, valid configured-key status, missing model,
      provider failure, and fallback combinations have deterministic tests.
    - API and browser labels do not imply that OpenAI Task Models are selectable or
      live-verified when they are not.
    - Focused task-provider/service/API tests and the complete verifier pass.

13. **Untangle the conversation critical path before extending voice.**

    **Why now:** `ConversationService.create_turn` has cyclomatic complexity 49 and
    combines binding resolution, model/settings selection, persistence, job creation,
    context inputs, and follow-up work. `ContextService.plan` is another grandfathered
    critical path. Both will be touched by streaming speech, interruption, and
    turn-taking, so leaving them tangled raises the cost and risk of every voice step.

    **Required work:**

    - After item 1 establishes the binding invariant, extract small application
      services/helpers for turn validation and resolution, transactional persistence,
      generation-job construction, and post-turn follow-up scheduling.
    - Split context loading, protected-section budgeting, optional-section selection,
      transcript-floor selection, and final prompt assembly without changing their
      ordering or truthful context notices.
    - Keep HTTP routes, provider adapters, persistence, and event delivery separate.
      This is a behavior-preserving refactor, not a new orchestration framework.
    - Remove the `# noqa: C901` exemptions from `create_turn` and
      `ContextService.plan`; do not add new complexity exemptions elsewhere.
    - Update architecture/debt documentation and add narrow characterization tests
      wherever existing behavior is not already pinned down.

    **Done when:**

    - Both target functions satisfy the repository complexity ceiling of 15 without
      suppressions.
    - Prompt order, history floor, memory/lore selection, title generation, context
      notices, job/event ordering, cancellation, fallback, and recovery behavior are
      unchanged in focused tests.
    - The complete verifier and deterministic human-experience scenarios pass.

### 1D. Other ready work

14. **Bring direct media actions under measured-capacity admission.** The direct
    image buttons still use legacy provider settings through a disclosed manual
    plan, so their demand is unknown and they bypass catalog-estimate admission.
    They do take the shared-resource lease, but two different paths to the same
    result is the last major split between direct and conversational
    generation. Source: `docs/debt-register.md`;
    `docs/human-experience-realignment-plan.md` baseline gap 8.

15. **Move provider helper internals off legacy low-level inputs.** Routes use
    SQLAlchemy repositories and unit-of-work boundaries, but some provider
    helpers still take HTTP/SQLite-shaped arguments. This is the remaining
    inconsistency in the persistence boundary. Source: `docs/debt-register.md`.

16. **Lift provider-specific settings out of persona and UI records.** Provider
    details are embedded directly in those records, which couples persona data
    to whichever provider happened to be configured. Source:
    `docs/debt-register.md`.

17. **Decide whether turn event replay needs a durable log.** Replay is bounded
    and process-local today. That is honest and sufficient for a single-process
    private-LAN deployment; it is listed so the limitation stays visible rather
    than being discovered during a future multi-process change. Source:
    `docs/debt-register.md`.


## 2. Needs decision

Implementable once an owner policy choice is recorded.

Nothing is parked here that the owner has not seen. The proactive-message
question that sat here was answered on 2026-08-16: the reply stays first. See
section 6 and item 6.

18. **Automatic expiry for rejected and forgotten memory.** Retention is durable
    and users can permanently delete individual or bulk records, but there is no
    administrator-approved automatic expiry policy. The code change is small;
    the retention period and its defaults are the decision. Source:
    `docs/debt-register.md`, `docs/memory.md`.

19. **Semantic memory retrieval.** Retrieval is lexical full-text search plus
    recency. Semantic retrieval remains an optional future interface and is
    deliberately not implied anywhere in the product. Adding it is a scope
    decision, not a blocked task. Source: `docs/debt-register.md`.

20. **Workspace-shared lore.** Lore is persona-scoped, so an entry used by
    several personas in a workspace has to be authored more than once. Sharing
    is a product decision about who owns an entry and what happens when one
    persona edits it, not a schema problem. Source:
    `docs/autonomous-decision-log.md` D5, `docs/debt-register.md`.

21. **Whether Task Model roles may send conversation-derived text to OpenAI.**
    The adapter exists and is deliberately not selectable in settings. Until
    this is answered the UI stays local-only and must not advertise OpenAI as a
    usable provider. Source: `docs/task-models.md`, open question 5 below.

## 3. Blocked - operator

Phase 6 of `docs/human-experience-realignment-plan.md`, deferred by operator
decision. These are the five open voice-core items, and they are the highest
product priority once unblocked: the roadmap states that additional catalog
breadth does not take priority over them.

An important distinction, because it changes what can start early: items 12-14
are provider-neutral infrastructure and could be built against the existing
Kokoro path behind a flag. Only items 15-16 genuinely require the approved
listening decision. Step 15 cannot select a provider until that decision
exists, and no unverified provider support may be advertised in the meantime.

22. **Streaming TTS.** Begin playback before a complete response file exists.
    Today synthesis must finish before audio starts.

23. **Automatic end-of-turn detection.** Detect that the user has stopped
    speaking, with push-to-talk retained as a dependable fallback rather than
    replaced.

24. **True barge-in.** Interrupting playback must also stop the superseded
    provider work, not just mute the output.

25. **Approved quality-first and local fallback chains for TTS and STT**, with
    compact user-facing degradation notices. Requires the approved provider
    chain from item 16.

26. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 15 and deferred roadmap steps 10-13.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance.

27. **Deployment guard migration.** Complete the one-time supervised migration
    from the legacy direct guard, then prove remote guard update, guard
    rollback and re-update, one-container deployment, and the final installed
    browser image journeys. Source: `docs/roadmap.md` step 24, ADR 0025,
    `docs/human-experience-realignment-plan.md`.

28. **Installed acceptance for picture-message delivery.** Roadmap step 22 is
    published but not accepted on the real topology. Source: `docs/roadmap.md`,
    ADRs 0019-0020.

29. **Installed acceptance for conversation cleanup.** Roadmap step 23, same
    situation. Source: `docs/roadmap.md`, ADR 0021.

30. **Identity-stage latency and capacity acceptance.** Unaccepted until the
    real verifier, consented references, and a compatible ComfyUI identity
    workflow are deployed together. The completed step 20 base media checks are
    explicitly not substitute evidence. Source: `docs/debt-register.md`,
    `docs/deployment-acceptance.md`.

31. **Live capacity tuning for the deployment GPU.** Timing and capacity
    behavior under real memory limits remains deployment acceptance work.
    Source: `docs/roadmap.md` step 18C.

32. **Installed acceptance for conversational image editing.** Delivered under
    ADR 0029 and covered by contract, API, and gate tests, but no installed
    browser journey has confirmed the confirmation card, the reference the
    planner chose, or a real ComfyUI edit workflow on the deployment. Until then
    the feature is published, not accepted. Source: ADR 0029.

## 5. Temporary by agreement

Deliberately shipped with a planned end, so it is never mistaken for a permanent
surface.

- **Remove the routing tester** once preset routing is demonstrably stable on
  the deployment. It exists to make routing-card authoring observable rather
  than guesswork; when routing is trusted it is clutter. Source: ADR 0030,
  `docs/settings-experience.md`.

## 6. Not advertised

Deliberately absent. Listed so none of it is mistaken for a regression, and so
no stub is ever shipped in its place. See `docs/debt-register.md`.

- Realtime and streaming TTS - no endpoint is advertised until step 15 lands.
- Local speech-to-text - the setting is retained for migration compatibility
  but is disabled in the UI until a real adapter exists.
- Realtime turn detection, partial transcripts, barge-in, and speech fallback.
- Multi-reference identity fusion and automatic mask creation.
- Preset discovery, ratings, or a shared registry. Items 4 and 5 deliver a file
  an operator can move deliberately; it is not a distribution channel.
- Identity resemblance produced by resampling until a comparison passes. See
  ADR 0031: comparison is advisory measurement, never the mechanism.
- Multi-replica or public deployment. Login throttling and metrics are
  in-process because the supported deployment is one private-LAN application
  process; changing that requires shared rate-limit and telemetry
  infrastructure and a new threat model.
- Planning a picture before the persona replies. Decided 2026-08-16. It would
  add a Task Model call to the critical path of every turn, including turns
  with no picture in them, and real-time voice conversation is the direction the
  product is heading. Running the free pattern gate first and planning early
  only for messages it flags is a plausible future version of this, and is
  explicitly not being built yet: the owner does not expect a deterministic
  filter to catch requests made conversationally, by hint or suggestion rather
  than by trigger word, and a filter that misses is worse than no filter here.
  Alignment is being pursued from the other direction instead, in item 6.
- Adopting mem0, Zep, Letta, or Cognee. They optimize recall; the complaint was
  noise. Each adds a service and an embedding model to a GPU budget already
  under contention, and none provides per-persona access boundaries.
- Semantic or vector lore retrieval, for the same reason. Keyword matching is
  predictable and debuggable, and the preview route makes it tunable.
- Grants, principals, and multi-tenancy for memory. One human and a handful of
  personas; persona scoping already delivers that isolation.
- Merging the old Memory v3 branch wholesale. Its useful immutable-binding idea
  is delivered independently by ADR 0032; the branch has materially diverged and
  reuses migration number `0019` for a different schema.
- Document ingestion. Chunking, versioning, citations, and retrieval is a larger
  product than everything else on this list combined.
- Autonomous persona life simulation. Generated backstory and off-screen events
  are out of scope in the persona depth spec.

## 7. Assumptions

Choices made where the request was ambiguous. Overturn any of these freely.

- Precision beats recall for memory. A missed fact is cheaper than a wrong one
  entering context as truth.
- The conversation is the product. Optional prompt material yields before
  conversation history does, at every window size.
- The platform decides, the model proposes. No task model selects scope,
  resources, or which lore fires.
- A setting that saves must change runtime behavior, or it must be refused at
  save time rather than silently substituted later.
- Persona material is authored, never generated or inferred from conversation.
- Real-time voice conversation is the direction. Anything that adds latency to
  every turn is measured against that, and a per-turn cost needs a reason
  stronger than the feature it buys. Work that happens after the reply, or
  overnight, is cheap by comparison and is where new behaviour should go first.

## 8. Open questions

For the operator, when convenient. None of these blocks the Ready list.

1. Should lore be shareable across personas in a workspace, or stay
   persona-scoped? Currently persona-scoped.
2. After the deploy, is 8k context affordable alongside speech and image
   generation on the 12 GB card? Measured behavior beats the estimate.
3. Is there an appetite for a second chat provider, or is Ollama the permanent
   local boundary?
4. Should rejected and forgotten memories expire automatically, and after how
   long?
5. May Task Model roles send conversation-derived text to OpenAI, or must these
   roles remain local-only? Until explicitly approved, the UI remains local-only
   and must not advertise OpenAI as selectable.
6. When the same persona is linked to multiple workspaces, should that persona's
   approved memories follow it across workspaces, or should workspace plus persona
   be a hard intersection? Current persona-scoped memories follow the persona;
   changing this later would require a migration and clear UI wording.

## Working rules

From `AGENTS.md`, repeated here because they decide whether an item counts as
done:

- Foundation-first. If a backlog item sits on a faulty foundation, fix or
  isolate the foundation instead of layering onto it.
- A saved setting is not complete until a test proves it changes runtime
  behavior.
- Never advertise placeholders, stubs, modeled state, or unverified provider
  support as working features.
- Update the product, architecture, security, testing, operations, roadmap, and
  debt documents in the same change as the behavior they describe.
- Record durable architectural choices as ADRs in `docs/decisions/`. The next
  free number is 0033.
- Run `python scripts/audit_public_repo.py` before every public commit. This
  file must never contain deployment addresses, hostnames, user-specific paths,
  hardware inventories, or account identifiers.
