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

Last full verifier run: passed, 2026-08-17.

## Status vocabulary

- **Ready** - no external dependency; can be started in this repository now.
- **Decided** - the choice has been made and recorded; the work it created is
  in the Ready list.
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
consistent: nothing above `app/database.py` speaks sqlite3 any more. A persona's
voice settings are keyed by provider rather than held in columns named after
one; see `docs/persona-authoring.md`. Turn event replay stays in memory by
decision, with the single-process assumption it rests on enforced at startup;
see ADR 0034.

**This section is complete.** Everything remaining needs an owner decision, an
operator evaluation, or the deployment.

### 1E. Identity-preserving picture workflows

Owner-requested 2026-08-16. ADR 0031 says resemblance must come from a declared
structural mechanism, and the record for it existed, but the feature had never
worked end to end. Every fault below passed the tests it had, which is why each
is recorded rather than quietly fixed. The reasoning is in
[ADR 0035](docs/decisions/0035-shipped-identity-workflows.md).

**This section is complete in this repository.** What remains is the installed
browser journey on the real deployment, described below.

Stage 0 is delivered. The workflow inspection response no longer drops
`request_input_candidates`, so guided identity setup can be completed over real
HTTP rather than only in a service call. A profile save no longer resets the
conditioning mechanism, the comparison retry switch, and a persona's preferred
recipes: the browser sends the whole profile, which is what a PUT means, and
that is pinned at the request-body level rather than at the view. A write from a
stale copy is refused instead of silently winning, because two surfaces write
this profile. The identity behavior card names how the face is produced, and the
comparison threshold moved behind the advanced disclosure where
`docs/settings-experience.md` already said it was.

Stage 2 is delivered, except for the declared model architecture, which has no
consumer until templates arrive and moves there rather than shipping as a field
that only round-trips. A ComfyUI graph carries the checkpoint it was saved with,
so a preset could name one base model and render another; `checkpoint_bindings`
now writes the preset's model into the graph, guided setup binds it when there
is exactly one such input, and a preset whose graph bakes a different checkpoint
is refused at save time naming both files. A preset created from an existing
model no longer claims `reference_adapter` for a catalog that cannot supply it,
and planning proves the mechanism from the graph it selects rather than from a
stored guess. A workflow filling an open slot must declare the operation being
requested, so an image-to-image identity graph is no longer attached to a
generate request and then failed at upload time.

Stage 3 is delivered. Two known-good ComfyUI graphs ship with the product -
PhotoMaker v2 and InstantID, both SDXL, both `reference_adapter` - with their
node IDs fixed and their bindings declared by construction, so nothing asks
which input receives the prompt or the reference. Inspection changed role for
them: it verifies that the node types and named files are installed, and states
plainly what it cannot see, because an identity model chosen by device rather
than by a named input is invisible to `/object_info`. A model resource may
declare its family, which is what lets a mismatch be marked before it wastes a
generation; the mismatch is shown rather than hidden. Installing pairs the graph
with a chosen model, records where it came from, and never rewrites a graph
somebody may have tuned. A technique that only conditions when a particular word
is in the prompt now declares that word and the prefix that supplies it, so it
cannot quietly return an ordinary picture. Importing a graph of your own is
still there, behind a disclosure.

Stage 4 is delivered. `identity_pass` - generate the picture, then replace the
face - now runs. It failed deterministically before: the second pass's bindings
were written at the top level and injected into the first pass's graph, where
those nodes do not exist, and a recipe whose identity capability lived in a
later pass could not satisfy the requirement at all because coverage ignored
later passes. Bindings are now assigned per pass rather than merged, the
reference goes to the pass whose graph has the nodes for it, and a capability a
later pass provides counts. A workflow may declare that it takes no prompt,
which a face swap genuinely does not - its only string widgets are face indexes,
and binding the request into one would be worse than having no binding. A
ReActor template ships for it, installable straight into a recipe as a second
pass so nobody hand-edits a definition. The settings control offers exactly the
mechanisms this catalog can apply.

A named file that is not installed is offered as a choice from what ComfyUI does
have, and the choice is written into the graph. A downloaded model keeps
whatever name its source gave it - several arrive as
`diffusion_pytorch_model.safetensors` - and asking somebody to rename a file to
match a shipped graph would be the hand-editing templates exist to remove.

### 1E-a. First identity-conditioned picture - **Blocked - deployment**

Install the
PhotoMaker v2 template against an SDXL photoreal checkpoint, add one approved
reference, and ask for a picture. That is the first honest end-to-end proof, and
no test here can substitute for it.
**Done when** a picture with a recognizable face exists and its generation log
shows the conditioning stage.

Back in this repository, stage 5 is delivered, and it was framing rather than code. `docs/persona-visual-identity.md`
now says what a comparison service is actually for: measuring how much likeness a
checkpoint family costs, run deliberately and then turned off, and choosing a
threshold from a measurement rather than from a guess with a decimal point in it.
Leaving it on as a gate is the worse proposition it always was.

The reasoning behind all of this is recorded in
[ADR 0035](docs/decisions/0035-shipped-identity-workflows.md).


### 1F. Work the 2026-08-17 decisions created

Each of these exists because an owner decision made it buildable. Nothing here
needs the deployment or a further choice.

The owner restated the goal on 2026-08-17 and it is worth having in one place,
because several items below only make sense against it: **the persona should
feel real, and an explicit conversation should be fully contained on this
machine with no cloud service involved.** Chat, images, and the voice you hear
are already local. The voice you speak is not - see item 1F-5, which is the only
thing standing between this product and the goal as stated.

Order, given that the owner mostly types today: 1F-1 first because it closes a
hole for the price of some words, then 1F-3 and 1F-4 because they are what make
a persona feel like itself, then 1F-5, then 1F-2.

1. **Settle the local-only Task Model boundary.** The OpenAI adapter exists and
    is not selectable. Make that a stated constraint rather than an accident of
    the UI: refuse the provider at save time for every task role, say why, and
    record it where somebody would look before trying to enable it.
    Same change covers the microphone: until 1F-5 lands, the interface must say
    where a recording goes, next to the button that sends it, rather than only
    in a settings page nobody is reading mid-conversation.
    **Done when** selecting OpenAI for a task role is refused by name, the
    documents describe it as a decision rather than an omission, and nobody can
    record a turn without having been told where the audio goes.

2. **Copy a lore entry from another persona.** Same workspace only. The copy
    belongs to the persona that took it, and the interface says plainly that
    later edits to the original will not follow - otherwise somebody will expect
    them to.
    **Done when** an entry can be copied in one action, and editing either copy
    provably leaves the other alone.

3. **Semantic memory retrieval.** A small local embedding model, memories
    embedded on write, the question embedded on read, and keyword search kept
    alongside so exact matches still win. It degrades to keyword-only, and says
    so, when no embedding model is installed - a missing model must not break
    recall.
    **Done when** a memory is found by a question that shares none of its words,
    an exact keyword match still ranks first, and the added time is recorded
    rather than assumed.

4. **Several reference photos, used together.** PhotoMaker stacks a batch into a
    stronger likeness; InstantID uses one. A template declares how many it can
    use, the executor uploads each and writes the right filename into the right
    binding, and the provenance record pins the whole set by checksum rather
    than a single photo.
    **Done when** a persona with three approved photos produces a picture whose
    log names all three.

5. **Local speech-to-text.** Transcription is OpenAI-only today: the service
    refuses anything else, so holding the microphone button sends audio off the
    machine. That is the one place the product contradicts the stated goal, and
    for the conversations this is for it is a problem twice over - privacy, and
    the provider's own usage policy. Whisper runs locally at roughly 250 MB,
    smaller than the speech synthesis already installed.
    **Done when** a spoken turn completes with no network request leaving the
    machine, and choosing cloud transcription is a deliberate act rather than
    the only option.

## 2. Decided

Answered by the owner on 2026-08-17. Each one produced either a settled
constraint or an item in section 1F below; none of them is open any more.

1. **Rejected and forgotten memory never expires automatically.** Manual
    deletion, individually or in bulk, stays the only way a record leaves.
    Keeping the discard pile is deliberate: it is what shows a persona
    re-proposing something already refused. Source: `docs/memory.md`.

2. **Memory retrieval gets real semantic search.** Memories are embedded when
    written, the question is embedded when asked, and keyword search stays
    alongside so an exact match still wins.

    This answer was first recorded as an overnight term-expansion scheme,
    because the question put to the owner presented the per-turn cost as a real
    trade-off. It is not one. An embedding model is around 275 MB against a
    chat model of four to five gigabytes, and embedding one question takes
    single-digit milliseconds against a turn that already takes seconds. The
    original framing was wrong, the owner reasonably chose the conservative
    side of a choice that should never have been offered, and the correction is
    recorded here rather than quietly applied. Becomes item 1F-3.

3. **Lore is shared by copying, not by reference.** An entry can be copied from
    another persona in the same workspace, and the copy belongs to the persona
    that took it. Edits never propagate. Sharing live would mean editing for one
    persona and silently changing another. Becomes item 1F-2.

4. **Task Model roles stay local.** No conversation-derived text goes to OpenAI
    for titles, summaries, memory extraction, picture planning, or scene
    proposals. The adapter is not selectable and must not be advertised.
    Becomes item 1F-1.

Also settled, from the open questions:

- **Memories follow the persona across workspaces.** One persona is one
    continuous person; a memory formed in one workspace is available in every
    workspace that persona is linked to. No migration needed.
- **Ollama is not being replaced, and the provider seam stays.** Nothing is
    built against it now; the indirection is kept so a second runtime later is a
    change rather than a rewrite.
- **A persona may have several approved reference photos, used together.**
    Becomes item 1F-4.

## 3. Blocked - operator

Phase 6 of `docs/human-experience-realignment-plan.md`. These were the five open
voice-core items and the highest product priority once unblocked, and three of
them are now delivered.

What separated the three from the two is whether a thing implies choosing a
speech provider. Streaming, interruption, and end-of-turn detection are
properties of the transport and the browser; they were built against the
existing Kokoro path with no provider claimed, and are recorded as ADRs 0036 to
0038. Items 8 and 9 are the decision itself, and neither can move until an
operator listening session has happened. No unverified provider support may be
advertised before it exists.

Everything delivered here is implemented, not accepted: none of it has run on
the installed deployment. See `docs/deployment-acceptance.md`.

5. **Streaming TTS - delivered 2026-08-17.** Playback begins on the first piece
    of audio rather than the finished file, so the silence before a persona
    speaks is no longer the whole synthesis time. The recording is still stored
    for replay, and a stream nobody waits for stores nothing. Formats that
    cannot start early - WAV carries its length in a header nothing can fill in
    halfway through - keep the completed-file path and say so rather than
    pretending. See
    [ADR 0037](docs/decisions/0037-speech-starts-before-it-is-finished.md).
    Built against the local Kokoro path; it is not accepted on the installed
    deployment and does not choose a provider.

6. **Automatic end-of-turn detection - delivered 2026-08-17.** Hands-free
    listening is a setting, off by default; with it on a tap starts the
    microphone and the product decides when the turn ended. Holding the button
    is unchanged and measures no level at all, because the release is the only
    decision here that can never be wrong. Silence nobody has spoken into never
    ends a turn, the speech and silence thresholds are separated so a voice near
    the line cannot make the decision flap, and a minute is the ceiling. See
    [ADR 0038](docs/decisions/0038-deciding-when-somebody-has-finished-talking.md).
    It listens to loudness, not to language: nothing is transcribed while
    somebody is still speaking.

7. **True barge-in - delivered 2026-08-17.** Interrupting playback used to mute
    the browser and leave the provider generating audio nobody would hear, then
    write it and rotate the audio cache to make room for it. The browser now
    aborts the synthesis request, the server watches for that and stops reading
    the provider response mid-body, and a cancelled synthesis writes nothing.
    See [ADR 0036](docs/decisions/0036-interrupting-speech-stops-the-work.md).
    This is manual interruption done properly. It is not the product noticing
    that somebody has started talking over it, which nothing here does.

8. **Approved quality-first and local fallback chains for TTS and STT**, with
    compact user-facing degradation notices. Requires the approved provider
    chain from item 16.

9. **Repeatable provider evaluation** on latency, reliability, and blind
    listening criteria - not configuration readiness alone. This is the
    evaluation that unblocks item 15 and deferred roadmap steps 10-13.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance.

10. **Deployment guard migration.** Complete the one-time supervised migration
    from the legacy direct guard, then prove remote guard update, guard
    rollback and re-update, one-container deployment, and the final installed
    browser image journeys. Source: `docs/roadmap.md` step 24, ADR 0025,
    `docs/human-experience-realignment-plan.md`.

11. **Installed acceptance for picture-message delivery.** Roadmap step 22 is
    published but not accepted on the real topology. Source: `docs/roadmap.md`,
    ADRs 0019-0020.

12. **Installed acceptance for conversation cleanup.** Roadmap step 23, same
    situation. Source: `docs/roadmap.md`, ADR 0021.

13. **Identity-stage latency and capacity acceptance.** Unaccepted until the
    real verifier, consented references, and a compatible ComfyUI identity
    workflow are deployed together. The completed step 20 base media checks are
    explicitly not substitute evidence. Source: `docs/debt-register.md`,
    `docs/deployment-acceptance.md`.

14. **Live capacity tuning for the deployment GPU.** Timing and capacity
    behavior under real memory limits remains deployment acceptance work.
    Source: `docs/roadmap.md` step 18C.

15. **Installed acceptance for conversational image editing.** Delivered under
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

- Nothing about speech-to-text belongs on this list any more. Local
  transcription was here as deliberately absent; it is item 1F-5, because the
  stated goal of a fully local conversation cannot be true without it.
- Partial transcripts. Nothing is transcribed while somebody is still speaking;
  end-of-turn detection listens to loudness, not to language.
- Speech provider fallback chains. Choosing what to fall back to is the
  listening decision, which has not been made.
- Speech from a provider this deployment has not evaluated. Streaming and
  interruption are built against the local Kokoro path; neither is a claim that
  any particular provider has been chosen or heard.
- Automatic mask creation. Multi-reference identity fusion was on this list and
  is not any more: it was approved on 2026-08-17 and is item 1F-4.
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
  predictable and debuggable, and the preview route makes it tunable. Memory
  retrieval takes a different route to the same goal - see item 1F-3 - which
  adds no model to the reply path and no service at all.
- A second chat provider. The seam that would allow one is kept deliberately;
  nothing is built against it, and Ollama is what runs.
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

1. After the deploy, is 8k context affordable alongside speech and image
   generation on the 12 GB card? Measured behavior beats the estimate, so this
   one genuinely waits for the deployment rather than for an opinion.

Six questions that sat here were answered on 2026-08-17. They are recorded as
decisions in section 2 rather than deleted, because the reasoning is worth more
than the answer.

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
  free number is 0039.
- Run `python scripts/audit_public_repo.py` before every public commit. This
  file must never contain deployment addresses, hostnames, user-specific paths,
  hardware inventories, or account identifiers.
