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
bindings, per-model prompt dialects, and conversation context for planning. See
`docs/media-catalog.md`. Every item below adds its own stages to the journal
rather than replacing it, so each one is reviewable from the picture it
produced.

**Phase 1 is complete.** Items may now be reordered if one is blocked, except
that nothing in Phase 3 starts before item 2 exists: the scene record is what
idea generation and library serving are keyed on.

This program does not displace the voice-core items in section 3. Those remain
the highest product priority and are blocked on an operator decision that has
not been made; this work is what can actually progress in the meantime.

1. **Generation Preset resource and migration.** Make the tested recipe the
   planned unit instead of assembling one from scored tags at request time.
   Source: ADR 0030.

   Done when:
   - A preset holds its graph or provider call, exact checkpoint and LoRAs at
     tested weights, sampler settings, permitted dimensions, dialect, declared
     inputs, and routing card.
   - Existing model, workflow, and LoRA plans migrate to implicit single-pass
     presets with no change to what they generate.
   - Automatic LoRA selection applies only to declared open slots, and existing
     compatibility edges still gate it.
   - Planning selects a preset and records which one, and why, in the journal.
   - The preset editor presents the prompt dialect as named fields. Dialects are
     configurable today only through the advanced default-settings JSON editor.

2. **Scene contract.** The Task Model emits a typed scene record rather than
   prompt text, so dialect becomes a rendering concern. Source: ADR 0030.

   Done when:
   - The capability contract carries subject, action, setting, wardrobe,
     framing, lighting, camera, and mood.
   - The model still cannot emit a provider, model, LoRA, workflow, filename,
     URL, or generation setting, and a test proves it.
   - The compiler consumes the scene; no prose prompt path remains in planning.
   - The scene is recorded in the journal.

3. **Shortlist routing with deterministic fallback.** Source: ADR 0030.

   Done when:
   - The platform hard-filters legal presets, then offers the model a bounded
     shortlist of opaque id, title, and routing card only.
   - An unknown or malformed preset id is rejected, and the deterministic score
     picks from the same shortlist on model failure or timeout.
   - The shortlist, the survivors, the filtered entries with reasons, and the
     winner with its reason are all in the journal.

4. **Routing tester in settings.** Deliberately temporary tooling, documented
   as such, to make preset authoring tractable. Source: ADR 0030.

   Done when:
   - Pasting a message shows which presets survived the filter, which was
     chosen, and why, using the same preview pattern as the lorebook.
   - It is labeled as a diagnostic that is expected to be removed, and it lives
     behind advanced disclosure per `docs/settings-experience.md`.

5. **Starter presets and the bundle format.** Source: ADR 0030.

   Done when:
   - A serialized preset bundle format exists and the built-in starter presets
     ship through it.
   - Starters install without overwriting operator-curated resources, following
     the ADR 0016 missing-kind rule.
   - A starter whose assets are not installed reports which are missing by name
     rather than failing at generation time.

6. **Multi-pass presets.** A preset declares its stages, including an identity
    pass and a detail pass. Today a second pass exists only as a correction
    retry. Source: ADR 0030.

    Done when:
    - Stages are declared, ordered, and each records its own journal entry.
    - Admission reserves the maximum stage estimate, preserving the ADR 0013
      rule that sequential stages are not summed.
    - A test covers a two-stage preset end to end.

7. **Persona Identity Spec, and comparison demoted to advisory.**
    Source: ADR 0031.

    Done when:
    - A persona carries a durable Identity Spec: approved reference set,
      canonical appearance text, the required conditioning mechanism, and the
      parameters tested for that persona.
    - Presets declare which identity mechanisms they implement, and a persona
      image plans only against a preset whose mechanism the spec supports,
      subject to the existing ADR 0018 fallback policy.
    - The comparison-driven retry loop is off by default and stays bounded when
      enabled.
    - A persona image generates with no verifier configured, is labeled
      `unverified`, and attempts no comparison.
    - No code path polls the verifier on a timer; readiness is on demand only.
    - `docs/persona-visual-identity.md` and `docs/media-catalog.md` describe
      comparison as optional post-hoc measurement throughout.

8. **Image library and ready-image serving.** The serving half of
    pre-generation, valuable against a hand-filled library. Source: ADR 0030.

    Done when:
    - Generated images can be retained in an owner-scoped library with their
      scene record, and images can be added to it by hand.
    - A persona picture request serves a matching ready image when one fits,
      and generates live when none does. Which happened is in the journal.
    - A proactive persona message is composed after its picture is chosen, so
      the text describes the image that will actually be attached.
    - No-repeat and freshness rules prevent serving the same image twice into
      one conversation.
    - A storage cap and a retirement policy exist, and the library is visible
      and deletable in settings.

9. **Settings consolidation.** This program must reduce settings surface, not
    grow it. Source: ADR 0030; `docs/settings-experience.md`.

    Done when:
    - Media Catalog is preset-first, with raw checkpoint, LoRA, and workflow
      inventory demoted to an advanced section.
    - A single Persona pictures surface holds references, identity spec, preset
      preferences, and the library, replacing the separate Visual Identity tab.
    - The count of top-level settings tabs does not increase, and the browser
      journeys cover the merged surface.

#### Phase 3 - library production

10. **Scene backlog and idea generation.** Propose scenes for a persona from
    its card, lorebook, and recent conversation themes. Source: ADR 0030.

    Done when a persona has a durable backlog of proposed scenes with states,
    each traceable to what suggested it, and nothing generates from it yet.

11. **Idle scheduler.** Produce backlog scenes on the background lane during
    quiet hours. Source: ADR 0030.

    Done when production runs only inside an operator-configured window and
    behind the existing capacity coordinator, a live turn preempts it, and a
    test proves an interactive job is never delayed behind batch work.

12. **Photo sets.** One idea, several frames sharing wardrobe, room, lighting,
    and seed family, varying pose and angle. Source: ADR 0030.

    Done when a set generates as a unit and serving can send several frames
    from the same set into one conversation.

13. **Preference weighting.** Deliberately simple and inspectable.
    Source: ADR 0030.

    Done when only explicit signals are recorded, the weights are visible and
    resettable in settings, and nothing in the product describes this as
    learning beyond what it measurably does.

14. **Preset export and import.** Source: ADR 0030.

    Done when export scrubs machine-specific values and previews exactly what
    will leave, import remaps referenced checkpoints and LoRAs against the local
    installation and clears imported VRAM estimates, and import states plainly
    that it executes another person's graph on this machine. No discovery or
    registry.

### 1B. Other ready work

15. **Bring direct media actions under measured-capacity admission.** The direct
    image buttons still use legacy provider settings through a disclosed manual
    plan, so their demand is unknown and they bypass catalog-estimate admission.
    They do take the shared-resource lease, but two different paths to the same
    result is the last major split between direct and conversational
    generation. Source: `docs/debt-register.md`;
    `docs/human-experience-realignment-plan.md` baseline gap 8.

16. **Move provider helper internals off legacy low-level inputs.** Routes use
    SQLAlchemy repositories and unit-of-work boundaries, but some provider
    helpers still take HTTP/SQLite-shaped arguments. This is the remaining
    inconsistency in the persistence boundary. Source: `docs/debt-register.md`.

17. **Lift provider-specific settings out of persona and UI records.** Provider
    details are embedded directly in those records, which couples persona data
    to whichever provider happened to be configured. Source:
    `docs/debt-register.md`.

18. **Decide whether turn event replay needs a durable log.** Replay is bounded
    and process-local today. That is honest and sufficient for a single-process
    private-LAN deployment; it is listed so the limitation stays visible rather
    than being discovered during a future multi-process change. Source:
    `docs/debt-register.md`.

19. **Second Task Model adapter.** The structured-output contract has exactly
    one implementation (Ollama). A second adapter is what proves the contract is
    a real boundary rather than a description of one client. No additional
    provider may be advertised until it implements the same contract. Source:
    `docs/debt-register.md`, `docs/task-models.md`.

## 2. Needs decision

Implementable once an owner policy choice is recorded.

20. **Automatic expiry for rejected and forgotten memory.** Retention is durable
    and users can permanently delete individual or bulk records, but there is no
    administrator-approved automatic expiry policy. The code change is small;
    the retention period and its defaults are the decision. Source:
    `docs/debt-register.md`, `docs/memory.md`.

21. **Semantic memory retrieval.** Retrieval is lexical full-text search plus
    recency. Semantic retrieval remains an optional future interface and is
    deliberately not implied anywhere in the product. Adding it is a scope
    decision, not a blocked task. Source: `docs/debt-register.md`.

## 3. Blocked - operator

Phase 6 of `docs/human-experience-realignment-plan.md`, deferred by operator
decision. These are the five open voice-core items, and they are the highest
product priority once unblocked: the roadmap states that additional catalog
breadth does not take priority over them.

An important distinction, because it changes what can start early: items 22-24
are provider-neutral infrastructure and could be built against the existing
Kokoro path behind a flag. Only items 25-26 genuinely require the approved
listening decision. Step 25 cannot select a provider until that decision
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
    chain from item 26.

26. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 25 and deferred roadmap steps 10-13.

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

## 5. Not advertised

Deliberately absent. Listed so none of it is mistaken for a regression, and so
no stub is ever shipped in its place. See `docs/debt-register.md`.

- Realtime and streaming TTS - no endpoint is advertised until step 25 lands.
- Local speech-to-text - the setting is retained for migration compatibility
  but is disabled in the UI until a real adapter exists.
- Realtime turn detection, partial transcripts, barge-in, and speech fallback.
- Multi-reference identity fusion and automatic mask creation.
- Preset discovery, ratings, or a shared registry. Item 14 delivers a file an
  operator can move deliberately; it is not a distribution channel.
- Identity resemblance produced by resampling until a comparison passes. See
  ADR 0031: comparison is advisory measurement, never the mechanism.
- Multi-replica or public deployment. Login throttling and metrics are
  in-process because the supported deployment is one private-LAN application
  process; changing that requires shared rate-limit and telemetry
  infrastructure and a new threat model.

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
