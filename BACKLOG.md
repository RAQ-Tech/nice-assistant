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

Items in the image generation program carry their own **Done when** list. Those
criteria are in addition to the verifier, never instead of it.

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

1. **Photo sets.** One idea, several frames sharing wardrobe, room, lighting,
    and seed family, varying pose and angle. Source: ADR 0030.

    Done when a set generates as a unit and serving can send several frames
    from the same set into one conversation.

2. **Preference weighting.** Deliberately simple and inspectable.
    Source: ADR 0030.

    Done when only explicit signals are recorded, the weights are visible and
    resettable in settings, and nothing in the product describes this as
    learning beyond what it measurably does.

3. **Preset export and import.** Source: ADR 0030.

    Done when export scrubs machine-specific values and previews exactly what
    will leave, import remaps referenced checkpoints and LoRAs against the local
    installation and clears imported VRAM estimates, and import states plainly
    that it executes another person's graph on this machine. No discovery or
    registry.

### 1B. Correctness work carried in from main

Recorded by a parallel session and verified against `main` at `0df1d89` on
2026-08-14. The reproductions are regression-test inputs, not review notes.
These come before the remaining foundation work below because each one is a
defect in behavior that already ships.

4. **Make chat workspace and persona bindings immutable.**

    **Why this is first:** a chat can currently be retargeted to another persona or
    workspace after it already has a transcript. The old persona's assistant replies
    then remain in the next model prompt. A cross-workspace persona can also be saved
    onto a chat before the next turn fails with `persona not found`. That makes the
    conversation boundary internally inconsistent and can leak one persona's context
    into another.

    **Required work:**

    - Make chat creation the authoritative point where `workspace_id` and
      `persona_id` are bound. A turn must never rewrite either field.
    - During a compatibility window, turn payloads may repeat the chat's current IDs,
      but reject mismatches before writing the user message, turn, job, or chat.
      Remove the redundant fields from the browser request and document their API
      deprecation/removal.
    - Stop `PATCH /api/v1/chats/{id}` from retargeting an existing chat. In the
      browser, selecting a different persona or workspace while a chat is active must
      create a clean chat with the new binding. Do not silently copy the old
      transcript; an explicit fork feature is separate future work.
    - Validate persona/workspace membership atomically at chat creation and in one
      reusable application-service boundary. Do not rely on a later context lookup to
      discover an invalid combination.
    - Add a data-preserving migration or compatibility repair for pre-existing
      inconsistent chats. Keep their transcripts readable and document the
      conservative rule used; do not delete or silently reattribute messages.
    - Record the binding decision in an ADR and update the conversation, API,
      browser, migration, security, and testing documentation it affects.
    - Do not merge `codex/human-experience-realignment` wholesale. Port only useful
      concepts: that branch is divergent and its `0019_memory_v3_identity_access`
      migration conflicts with the current `0019_persona_character_card` lineage.

    **Done when:**

    - A cross-workspace persona update is rejected and leaves the stored chat
      unchanged.
    - A per-turn workspace/persona mismatch is rejected before any durable row or
      event is created; repeating the bound IDs remains compatible if retained.
    - Changing persona in the UI starts a new chat, and no prior persona system
      material, lore, memories, user messages, or assistant messages enters its
      prompt.
    - Existing valid chats remain readable and can continue normally after migration;
      inconsistent legacy rows follow the documented non-destructive rule.
    - Focused service/API/browser tests cover the two verified reproductions, followed
      by the complete verifier and a deterministic human-experience scenario.

5. **Make Task Model readiness credential-aware and truthful.**

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

6. **Untangle the conversation critical path before extending voice.**

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

### 1C. Other ready work

7. **Bring direct media actions under measured-capacity admission.** The direct
    image buttons still use legacy provider settings through a disclosed manual
    plan, so their demand is unknown and they bypass catalog-estimate admission.
    They do take the shared-resource lease, but two different paths to the same
    result is the last major split between direct and conversational
    generation. Source: `docs/debt-register.md`;
    `docs/human-experience-realignment-plan.md` baseline gap 8.

8. **Move provider helper internals off legacy low-level inputs.** Routes use
    SQLAlchemy repositories and unit-of-work boundaries, but some provider
    helpers still take HTTP/SQLite-shaped arguments. This is the remaining
    inconsistency in the persistence boundary. Source: `docs/debt-register.md`.

9. **Lift provider-specific settings out of persona and UI records.** Provider
    details are embedded directly in those records, which couples persona data
    to whichever provider happened to be configured. Source:
    `docs/debt-register.md`.

10. **Decide whether turn event replay needs a durable log.** Replay is bounded
    and process-local today. That is honest and sufficient for a single-process
    private-LAN deployment; it is listed so the limitation stays visible rather
    than being discovered during a future multi-process change. Source:
    `docs/debt-register.md`.


## 2. Needs decision

Implementable once an owner policy choice is recorded.

**Compose a proactive persona message after its picture is chosen.** The library
can serve a ready picture, but a persona still writes its reply before anything
decides which image is attached. That is how "took Roofus for a walk" ends up
next to a beach photo.

The fix conflicts with a recorded decision. ADR 0021 keeps capability planning
off the reply critical path so a reply arrives fast; choosing the picture first
means planning before the persona speaks, which adds its latency to every
persona turn. Both are real: an instant reply that describes the wrong picture
is not obviously better than a slower one that describes the right picture.

The decision is which cost to pay, and the options are not equal work:

- Plan before the reply for persona chats only, accepting the added latency.
- Invert only when the picture comes from the library, since a lookup is fast
  and a generation is not. Mismatches remain possible whenever a picture is
  generated live.
- Leave the reply first and give the attachment its own caption composed after
  the picture is known, so the words next to the image always match it while the
  reply itself stays fast. This needs a new task-model role.

Recorded rather than guessed because it trades product feel against reply
latency, and because the third option adds a task role that should not appear
without intent. Source: ADR 0030, ADR 0021.

11. **Automatic expiry for rejected and forgotten memory.** Retention is durable
    and users can permanently delete individual or bulk records, but there is no
    administrator-approved automatic expiry policy. The code change is small;
    the retention period and its defaults are the decision. Source:
    `docs/debt-register.md`, `docs/memory.md`.

12. **Semantic memory retrieval.** Retrieval is lexical full-text search plus
    recency. Semantic retrieval remains an optional future interface and is
    deliberately not implied anywhere in the product. Adding it is a scope
    decision, not a blocked task. Source: `docs/debt-register.md`.

13. **Workspace-shared lore.** Lore is persona-scoped, so an entry used by
    several personas in a workspace has to be authored more than once. Sharing
    is a product decision about who owns an entry and what happens when one
    persona edits it, not a schema problem. Source:
    `docs/autonomous-decision-log.md` D5, `docs/debt-register.md`.

14. **Whether Task Model roles may send conversation-derived text to OpenAI.**
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

15. **Streaming TTS.** Begin playback before a complete response file exists.
    Today synthesis must finish before audio starts.

16. **Automatic end-of-turn detection.** Detect that the user has stopped
    speaking, with push-to-talk retained as a dependable fallback rather than
    replaced.

17. **True barge-in.** Interrupting playback must also stop the superseded
    provider work, not just mute the output.

18. **Approved quality-first and local fallback chains for TTS and STT**, with
    compact user-facing degradation notices. Requires the approved provider
    chain from item 16.

19. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 15 and deferred roadmap steps 10-13.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance.

20. **Deployment guard migration.** Complete the one-time supervised migration
    from the legacy direct guard, then prove remote guard update, guard
    rollback and re-update, one-container deployment, and the final installed
    browser image journeys. Source: `docs/roadmap.md` step 24, ADR 0025,
    `docs/human-experience-realignment-plan.md`.

21. **Installed acceptance for picture-message delivery.** Roadmap step 22 is
    published but not accepted on the real topology. Source: `docs/roadmap.md`,
    ADRs 0019-0020.

22. **Installed acceptance for conversation cleanup.** Roadmap step 23, same
    situation. Source: `docs/roadmap.md`, ADR 0021.

23. **Identity-stage latency and capacity acceptance.** Unaccepted until the
    real verifier, consented references, and a compatible ComfyUI identity
    workflow are deployed together. The completed step 20 base media checks are
    explicitly not substitute evidence. Source: `docs/debt-register.md`,
    `docs/deployment-acceptance.md`.

24. **Live capacity tuning for the deployment GPU.** Timing and capacity
    behavior under real memory limits remains deployment acceptance work.
    Source: `docs/roadmap.md` step 18C.

25. **Installed acceptance for conversational image editing.** Delivered under
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
- Preset discovery, ratings, or a shared registry. Item 3 delivers a file an
  operator can move deliberately; it is not a distribution channel.
- Identity resemblance produced by resampling until a comparison passes. See
  ADR 0031: comparison is advisory measurement, never the mechanism.
- Multi-replica or public deployment. Login throttling and metrics are
  in-process because the supported deployment is one private-LAN application
  process; changing that requires shared rate-limit and telemetry
  infrastructure and a new threat model.
- Adopting mem0, Zep, Letta, or Cognee. They optimize recall; the complaint was
  noise. Each adds a service and an embedding model to a GPU budget already
  under contention, and none provides per-persona access boundaries.
- Semantic or vector lore retrieval, for the same reason. Keyword matching is
  predictable and debuggable, and the preview route makes it tunable.
- Grants, principals, and multi-tenancy for memory. One human and a handful of
  personas; persona scoping already delivers that isolation.
- Merging the old Memory v3 branch wholesale. Its useful immutable-binding idea
  is carried as item 4; the branch has materially diverged and reuses migration
  number `0019` for a different schema.
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
  free number is 0032.
- Run `python scripts/audit_public_repo.py` before every public commit. This
  file must never contain deployment addresses, hostnames, user-specific paths,
  hardware inventories, or account identifiers.
