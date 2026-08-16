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
policy now has something consuming it, and photo sets generate several frames
from one shared scene with related seeds and arrive in a conversation together.
What happens to a picture after it is made is counted against the preset that
made it, and those counts reorder presets that already fit. A preset exports as
a shareable file, previewed field by field before it is written, and a file from
somebody else imports all-or-nothing after saying what it would do. What the
persona has been saying reorders retained pictures that already qualify, without
ever making one eligible; see ADR 0033.

**Section 1A is complete.** The
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

### 1B. Homepage and everyday visibility - complete

Owner-requested 2026-08-16. Loading the browser drops straight into the last
chat, so there is nowhere to see what the assistant is currently set up to do.
The pre-generation schedule is the sharp example: it spends GPU time overnight
and is currently only visible to someone who goes looking for it in settings.

Pre-generation is now an owner setting, so the control on the homepage has
something real to change, `#/` is reachable, the mark in the chat header goes
back to it, the page reports what is currently true, and the pre-generation
switch and schedule sit on it beside speech and memory mode.

**This section is complete.**

### 1C. Correctness work carried in from main

Recorded by a parallel session and verified against `main` at `0df1d89` on
2026-08-14. The reproductions are regression-test inputs, not review notes.
These come before the remaining foundation work below because each one is a
defect in behavior that already ships.

Chat workspace and persona bindings are now immutable; see ADR 0032 and
migration `0031`. Task Model readiness now separates adapter installation,
account credentials, and live verification, so a keyless profile no longer
reports itself ready; see `docs/task-models.md`. The conversation critical path
is untangled: `create_turn` and `ContextService.plan` both satisfy the
complexity ceiling without suppressions, and generation and follow-ups live in
`app/turn_pipeline.py`; see `docs/architecture.md`.

This section is complete.

### 1D. Other ready work

Direct media actions now declare their demand, so measured-capacity admission
applies to them; what remains split from conversational generation is selection,
not admission. See `docs/media-catalog.md`. The persistence boundary is
consistent: nothing above `app/database.py` speaks sqlite3 any more.


1. **Lift provider-specific settings out of persona and UI records.** Provider
    details are embedded directly in those records, which couples persona data
    to whichever provider happened to be configured. Source:
    `docs/debt-register.md`.

2. **Decide whether turn event replay needs a durable log.** Replay is bounded
    and process-local today. That is honest and sufficient for a single-process
    private-LAN deployment; it is listed so the limitation stays visible rather
    than being discovered during a future multi-process change. Source:
    `docs/debt-register.md`.


## 2. Needs decision

Implementable once an owner policy choice is recorded.

Nothing is parked here that the owner has not seen. The proactive-message
question that sat here was answered on 2026-08-16: the reply stays first. See
section 6 and item 6.

3. **Automatic expiry for rejected and forgotten memory.** Retention is durable
    and users can permanently delete individual or bulk records, but there is no
    administrator-approved automatic expiry policy. The code change is small;
    the retention period and its defaults are the decision. Source:
    `docs/debt-register.md`, `docs/memory.md`.

4. **Semantic memory retrieval.** Retrieval is lexical full-text search plus
    recency. Semantic retrieval remains an optional future interface and is
    deliberately not implied anywhere in the product. Adding it is a scope
    decision, not a blocked task. Source: `docs/debt-register.md`.

5. **Workspace-shared lore.** Lore is persona-scoped, so an entry used by
    several personas in a workspace has to be authored more than once. Sharing
    is a product decision about who owns an entry and what happens when one
    persona edits it, not a schema problem. Source:
    `docs/autonomous-decision-log.md` D5, `docs/debt-register.md`.

6. **Whether Task Model roles may send conversation-derived text to OpenAI.**
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

7. **Streaming TTS.** Begin playback before a complete response file exists.
    Today synthesis must finish before audio starts.

8. **Automatic end-of-turn detection.** Detect that the user has stopped
    speaking, with push-to-talk retained as a dependable fallback rather than
    replaced.

9. **True barge-in.** Interrupting playback must also stop the superseded
    provider work, not just mute the output.

10. **Approved quality-first and local fallback chains for TTS and STT**, with
    compact user-facing degradation notices. Requires the approved provider
    chain from item 16.

11. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 15 and deferred roadmap steps 10-13.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance.

12. **Deployment guard migration.** Complete the one-time supervised migration
    from the legacy direct guard, then prove remote guard update, guard
    rollback and re-update, one-container deployment, and the final installed
    browser image journeys. Source: `docs/roadmap.md` step 24, ADR 0025,
    `docs/human-experience-realignment-plan.md`.

13. **Installed acceptance for picture-message delivery.** Roadmap step 22 is
    published but not accepted on the real topology. Source: `docs/roadmap.md`,
    ADRs 0019-0020.

14. **Installed acceptance for conversation cleanup.** Roadmap step 23, same
    situation. Source: `docs/roadmap.md`, ADR 0021.

15. **Identity-stage latency and capacity acceptance.** Unaccepted until the
    real verifier, consented references, and a compatible ComfyUI identity
    workflow are deployed together. The completed step 20 base media checks are
    explicitly not substitute evidence. Source: `docs/debt-register.md`,
    `docs/deployment-acceptance.md`.

16. **Live capacity tuning for the deployment GPU.** Timing and capacity
    behavior under real memory limits remains deployment acceptance work.
    Source: `docs/roadmap.md` step 18C.

17. **Installed acceptance for conversational image editing.** Delivered under
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
- Preset discovery, ratings, or a shared registry. Export and import deliver a file
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
  free number is 0034.
- Run `python scripts/audit_public_repo.py` before every public commit. This
  file must never contain deployment addresses, hostnames, user-specific paths,
  hardware inventories, or account identifiers.
