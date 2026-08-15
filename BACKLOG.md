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
bindings, per-model prompt dialects, and conversation context for planning. The
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

1. **Produce approved scenes in the background.** The quiet-hours policy and
    its readiness report exist and are tested, but nothing consumes them: no
    approved scene is ever made. Source: ADR 0030.

    Done when:
    - Approved scenes are generated when the policy allows it, on the media lane
      as bulk work so a requested picture is always chosen first.
    - Entries move to `generating` and then `done` with their picture, and a
      failure returns them to `approved` rather than stranding them.
    - A test proves an interactive job is never delayed behind background work.

    First decide: a background picture has no conversation, but a capability
    request is currently tied to a chat and an assistant message for its
    attachment. Either such requests become chat-less, or production writes
    straight to the library without a capability request. That choice shapes the
    rest and should be made deliberately rather than discovered while wiring.


2. **Photo sets.** One idea, several frames sharing wardrobe, room, lighting,
    and seed family, varying pose and angle. Source: ADR 0030.

    Done when a set generates as a unit and serving can send several frames
    from the same set into one conversation.

3. **Preference weighting.** Deliberately simple and inspectable.
    Source: ADR 0030.

    Done when only explicit signals are recorded, the weights are visible and
    resettable in settings, and nothing in the product describes this as
    learning beyond what it measurably does.

4. **Preset export and import.** Source: ADR 0030.

    Done when export scrubs machine-specific values and previews exactly what
    will leave, import remaps referenced checkpoints and LoRAs against the local
    installation and clears imported VRAM estimates, and import states plainly
    that it executes another person's graph on this machine. No discovery or
    registry.

### 1B. Other ready work

5. **Bring direct media actions under measured-capacity admission.** The direct
    image buttons still use legacy provider settings through a disclosed manual
    plan, so their demand is unknown and they bypass catalog-estimate admission.
    They do take the shared-resource lease, but two different paths to the same
    result is the last major split between direct and conversational
    generation. Source: `docs/debt-register.md`;
    `docs/human-experience-realignment-plan.md` baseline gap 8.

6. **Move provider helper internals off legacy low-level inputs.** Routes use
    SQLAlchemy repositories and unit-of-work boundaries, but some provider
    helpers still take HTTP/SQLite-shaped arguments. This is the remaining
    inconsistency in the persistence boundary. Source: `docs/debt-register.md`.

7. **Lift provider-specific settings out of persona and UI records.** Provider
    details are embedded directly in those records, which couples persona data
    to whichever provider happened to be configured. Source:
    `docs/debt-register.md`.

8. **Decide whether turn event replay needs a durable log.** Replay is bounded
    and process-local today. That is honest and sufficient for a single-process
    private-LAN deployment; it is listed so the limitation stays visible rather
    than being discovered during a future multi-process change. Source:
    `docs/debt-register.md`.

9. **Second Task Model adapter.** The structured-output contract has exactly
    one implementation (Ollama). A second adapter is what proves the contract is
    a real boundary rather than a description of one client. No additional
    provider may be advertised until it implements the same contract. Source:
    `docs/debt-register.md`, `docs/task-models.md`.

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

10. **Automatic expiry for rejected and forgotten memory.** Retention is durable
    and users can permanently delete individual or bulk records, but there is no
    administrator-approved automatic expiry policy. The code change is small;
    the retention period and its defaults are the decision. Source:
    `docs/debt-register.md`, `docs/memory.md`.

11. **Semantic memory retrieval.** Retrieval is lexical full-text search plus
    recency. Semantic retrieval remains an optional future interface and is
    deliberately not implied anywhere in the product. Adding it is a scope
    decision, not a blocked task. Source: `docs/debt-register.md`.

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

12. **Streaming TTS.** Begin playback before a complete response file exists.
    Today synthesis must finish before audio starts.

13. **Automatic end-of-turn detection.** Detect that the user has stopped
    speaking, with push-to-talk retained as a dependable fallback rather than
    replaced.

14. **True barge-in.** Interrupting playback must also stop the superseded
    provider work, not just mute the output.

15. **Approved quality-first and local fallback chains for TTS and STT**, with
    compact user-facing degradation notices. Requires the approved provider
    chain from item 16.

16. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 15 and deferred roadmap steps 10-13.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance.

17. **Deployment guard migration.** Complete the one-time supervised migration
    from the legacy direct guard, then prove remote guard update, guard
    rollback and re-update, one-container deployment, and the final installed
    browser image journeys. Source: `docs/roadmap.md` step 24, ADR 0025,
    `docs/human-experience-realignment-plan.md`.

18. **Installed acceptance for picture-message delivery.** Roadmap step 22 is
    published but not accepted on the real topology. Source: `docs/roadmap.md`,
    ADRs 0019-0020.

19. **Installed acceptance for conversation cleanup.** Roadmap step 23, same
    situation. Source: `docs/roadmap.md`, ADR 0021.

20. **Identity-stage latency and capacity acceptance.** Unaccepted until the
    real verifier, consented references, and a compatible ComfyUI identity
    workflow are deployed together. The completed step 20 base media checks are
    explicitly not substitute evidence. Source: `docs/debt-register.md`,
    `docs/deployment-acceptance.md`.

21. **Live capacity tuning for the deployment GPU.** Timing and capacity
    behavior under real memory limits remains deployment acceptance work.
    Source: `docs/roadmap.md` step 18C.

22. **Installed acceptance for conversational image editing.** Delivered under
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
- Preset discovery, ratings, or a shared registry. Item 4 delivers a file an
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
