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

Last full verifier run: passed, 2026-09-02.

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

**This section is complete.** What can still be started here is in 1G.

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
no test here can substitute for it. The owner confirmed on 2026-09-02 that the
PhotoMaker v2 node and model are installed on the deployment's ComfyUI, so
what remains is the template install, a photo, and the picture.
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
machine with no cloud service involved.** Chat, images, the voice you hear and -
since 1F-5 landed on 2026-08-17 - the voice you speak can all run here. Every
part of that goal is now built in this repository. What it waits on is the
Transcription setting being pointed at the Whisper service the deployment
already runs, which is a deployment step rather than work here; the steps are
in `docs/operations.md` under local speech services.

Order was: 1F-1 first because it closed a hole for the price of some words, then
1F-3 and 1F-4 because they are what make a persona feel like itself, then 1F-5,
then 1F-2. **All five are delivered.** What is left is deployment, not code:
a Whisper service on the LAN for 1F-5, and the installed identity journey that
1E-a already tracks.

1. **Settle the local-only Task Model boundary - delivered 2026-08-17.** The OpenAI adapter exists and
    is not selectable. Make that a stated constraint rather than an accident of
    the UI: refuse the provider at save time for every task role, say why, and
    record it where somebody would look before trying to enable it.
    Corrected on the owner's instruction the same day: cloud providers stay
    available, because plenty of people running this will want one and removing
    the option decides for them. What changed instead is that no default is
    cloud, nothing escalates to one by fallback, every provider control names
    which kind it is in the option itself, and the homepage carries a line per
    part of a conversation saying where it goes right now. The microphone says
    where a recording goes next to the button that sends it, and that note
    disappears on its own once 1F-5 lands.

2. **Copy a lore entry from another persona - delivered 2026-08-17.** The
    lorebook editor lists what personas sharing the workspace have written and
    copies one across in one action, with keywords, priority and matching rules
    intact. The copy belongs to the persona that took it: editing either one
    provably leaves the other alone, and deleting the original leaves the copy
    standing. The interface says so at the moment of copying, which is the only
    moment anybody is thinking about it. An entry whose title the persona
    already has is not offered again, so a list cannot fill with duplicates
    nobody meant to make.

3. **Semantic memory retrieval - delivered 2026-08-17.** A small local
    embedding model, vectors computed in the background, the question embedded
    on read, and keyword search kept in front so an exact match still wins. A
    weak match is dropped rather than ranked last. The reply path never goes
    looking for the model: it asks only once a background pass has reached it,
    so a deployment without one pays nothing per turn. See
    [ADR 0039](docs/decisions/0039-memories-found-by-meaning.md).
    `nomic-embed-text` was pulled onto the deployment on 2026-08-17, and the
    similarity floor was then set from measurement against it rather than from
    reasoning - the reasoned value would have dropped four of nine correct
    matches. ADR 0039 records the numbers.

4. **Several reference photos, used together - delivered 2026-08-17.** PhotoMaker stacks a batch into a
    stronger likeness; InstantID uses one. A template declares how many it can
    use, the executor uploads each and writes the right filename into the right
    binding, and the provenance record pins the whole set by checksum rather
    than a single photo.
    A workflow declares how many photos it can take by how many image inputs it
    binds; the photos cycle over those slots, so fewer photos than slots repeats
    rather than leaving one pointing at a file the provider does not have. Every
    photo used is pinned by checksum and re-checked before execution. The
    shipped PhotoMaker graph takes three, batched into the encoder; InstantID
    takes one.
    **Still needs the deployment** to confirm the likeness is actually better
    with three, which no test here can answer.

5. **Local speech-to-text - delivered 2026-08-17.** Transcription was
    OpenAI-only: the service refused anything else with a 501, so holding the
    microphone button either sent audio off the machine or did nothing, while
    the setting still accepted `local`. It now talks to a self-hosted Whisper
    service over HTTP in the shape OpenAI documents, which is what speaches,
    whisper.cpp's server and LocalAI all implement - the same arrangement the
    local speech path already has with Kokoro, so the URL policy, connection
    check and failure copy are the ones that already existed. No credential is
    sent to a service on this network, the address is held to the private-LAN
    policy at save time so "local" cannot mean a host on the internet, and a
    `text/plain` reply is accepted as the transcript it is. See
    [ADR 0040](docs/decisions/0040-a-spoken-turn-that-stays-here.md).
    **Needs on the deployment:** pointing Transcription at the Whisper service
    that already runs there - it speaks Wyoming on port 10300 - and, for a
    conversation at speaking pace, running it on the GPU with a model sized
    for it. The owner confirmed the container on 2026-09-02; the steps are in
    `docs/operations.md`. Until then transcription is OpenAI or off, and the
    microphone says which.

### 1G. Handing you the keys, continued

Agreed with the owner on 2026-09-02, in this order. The first two are the rest
of the 2026-08-28 mandate (item 0a in section 2); the last two are the work the
video decision (item 0b) left behind.

1. **The remaining settings groups, in the model-page shape - delivered
    2026-09-02.** The model
    pages proved the design language: a list of plain things, each opening a
    page of its own, sparse fields, prefills that say where they came from,
    guarded navigation, and at most one visible hint per page. Conversation,
    Voice, Personas and System still have the earlier shape - a card with an
    information icon on every row, and collapsed editors inside collapsed
    editors. Redo them the same way: each persona, each conversation model,
    each background role and each voice gets a page, and the group page is
    the list. Section ids and deep links stay, so nothing that names one
    breaks.
    Delivered: Conversation, Voice, Personas and System are lists or single
    sparse pages, a persona, a model and a background role each have a page
    with an address, help waits on hover, and a unit test holds every redone
    page to one visible hint and no icons. See `docs/settings-experience.md`.

2. **Persona identity as a switch and a photo - delivered 2026-09-02.** It
    was a settings area
    of its own, with mechanism dropdowns, a readiness card and a comparison
    threshold. On the persona's own page it should be one switch - looks like
    this photo - and the photo, with the machinery appearing only when it
    genuinely needs a human hand: a missing model, a workflow with no input to
    bind, a reference that failed review. The 2026-08-28 change made adding a
    photo one motion and trimmed the readiness card; this finishes the
    collapse.
    Delivered: the persona's page carries one switch, "Looks like this photo",
    and the photos; a photo from this device counts at once, a generated
    picture waits for a yes; the only line said out loud is the one that needs
    a hand, and it leads to the workflow setup. Persona Pictures shows the same
    card, with comparison folded beneath. See `docs/persona-visual-identity.md`.

3. **Delete the OpenAI video adapter after 2026-09-24.** Sora's API stops
    answering that day. Until then the adapter stays as a stored choice that
    saves as Off; afterwards it is dead code, and dead code is deleted rather
    than kept for a service that no longer exists. Dated, not blocked: nothing
    here can be done before that day.
    **Done when** no code path names the OpenAI video provider, a stored
    `openai` video choice still lands as Off, and the documents say video is
    local only without describing a cloud adapter.

4. **A shipped Wan 2.2 video template - delivered 2026-09-02.** The local
    video path existed, but
    nothing ships to run on it: a person must import a text-to-video graph of
    their own. Ship one the way the identity templates ship - node IDs fixed,
    bindings declared by construction, inspection verifying that the node
    types and named files are installed and saying plainly what it cannot
    see. Wan 2.2's nodes live in ComfyUI itself, so their names can be
    verified against the ComfyUI source; what needs the deployment is the
    model download and the first clip.
    Delivered: the template ships with its node IDs fixed and its prompt,
    negative, seed and model bindings declared, is offered on the video import
    card to video models only, and its check names the model file this ComfyUI
    has not downloaded together with the files it does have. The first clip is
    item 16 in section 4.

5. **The Pictures pages, in the model-page shape.** Chosen by the owner on
    2026-09-02 as the next work once the four above were done. Image
    Generation, Video Generation, Media Catalog and Persona Pictures are the
    last group in the earlier shape - cards with an information icon on
    every row, and folds inside folds. Redo them the way the rest was done:
    the group page is a list of plain things, each preset, workflow and LoRA
    opens a page of its own, help waits on hover, and the identity
    machinery stays behind the face.
    **Done when** every Pictures page opens to a list or to one sparse page,
    no page carries more than one visible hint or an information icon, the
    model page stays the door to a model, and the settings tests and browser
    journeys pass against the new shape.

6. **Fold Persona Pictures into the persona's page.** Chosen by the owner on
    2026-09-02. The persona's page carries the face now, and Persona Pictures
    holds the same card again beside preferred recipes, kept pictures, and
    the folded comparison tools. One page per persona, nothing twice:
    preferred recipes and kept pictures move under the persona, the
    comparison service, its outcome policy, manual comparison and the
    activity record fold under the face's own fold, and the Persona Pictures
    entry leaves the settings menu. Every deep link that named it lands on
    the persona instead. Belongs with item 5, since Persona Pictures is one
    of the four Pictures pages.
    **Done when** nothing a persona owns is shown on two pages, the settings
    menu has no Persona Pictures entry, and an identity block from a chat
    still lands on the persona's page.

## 2. Decided

Answered by the owner on 2026-08-17, with additions from the settings
conversation on 2026-08-26. Each one produced either a settled constraint or a
listed item; none of them is open any more.

0. **A model is an ingredient; a workflow is what you do with it** (owner,
    2026-08-26). Day-to-day settings live on the model's own page, which edits
    the model resource and the model's recipe together; the raw recipe list
    retreats into Operator tools for multi-recipe and diagnostic work. Shipped
    with the model pages; see `docs/media-catalog.md`. The same conversation
    decided the CivitAI lookup: wanted, searched by filename with the person
    picking the match, behind a consent popup that names civitai.com and
    offers cancel / ok / "don't show again". Shipped with the model pages.

0a. **"Handing you the keys" — the Apple-grade experience pass** (owner,
    2026-08-28). Executed as verified slices, each screenshot-driven and
    merged separately: the chat visual overhaul (one-row header, framed
    pictures, quiet chrome), in-chat picture steering ("Another take" /
    "Different look" with honest refusals), model sample thumbnails with the
    resource-save wire fix, and the Shape picker replacing typed resolution.
    Nothing from the mandate is still open.

0b. **Video is local-only** (owner, 2026-08-26). Sora's API shuts down
    2026-09-24 and no surviving cloud video service accepts this product's
    content, so no cloud option is offered through the UI; the cloud adapter
    stays in the code for a future service worth linking, and a stored cloud
    choice saves as Off. The local ComfyUI video path shipped with this
    decision; see `docs/media-catalog.md`. The work it left is item 1G-3
    and the first clip, item 16 in section 4.

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

4. **Task Model roles default to local, and cloud stays available.** The owner
    runs everything locally and wants that to be the default and never an
    accident; he does not want the option taken away from anybody else. So no
    default is cloud, nothing falls back to one on its own, each control names
    which kind it is, and the homepage says where each part of a conversation
    currently goes. Becomes item 1F-1.

5. **Kokoro is the voice** (owner, 2026-09-02). The two voice-core items that
    waited on a listening session are closed: the local Kokoro service is the
    speech provider, a Whisper service on the LAN is the ear, and no fallback
    chain is built because there is nothing local to fall back to. OpenAI
    speech and transcription stay available as options, named as leaving the
    machine, for people whose hardware cannot do better.

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
voice-core items and the highest product priority once unblocked. All five are
settled now: three delivered, two closed by the owner's decision of 2026-09-02.

What separated the three from the two is whether a thing implies choosing a
speech provider. Streaming, interruption, and end-of-turn detection are
properties of the transport and the browser; they were built against the
existing Kokoro path with no provider claimed, and are recorded as ADRs 0036 to
0038. Items 8 and 9 were the decision itself; the owner made it on 2026-09-02
(section 2, item 5) without a listening session, and both are closed below. No
unverified provider support may be advertised before it exists.

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

8. **Fallback chains for TTS and STT - closed by decision, 2026-09-02.** The
    owner chose Kokoro as the voice and a Whisper service on the LAN as the
    ear (section 2, item 5). There is nothing local to fall back to, and
    falling back to a cloud service would contradict a conversation that
    stays on this machine, so no chain is built. A service that is down says
    so in the words the product already has.

9. **Provider evaluation - closed by decision, 2026-09-02.** The listening
    session it waited on was the choice itself, and the owner made it without
    one: Kokoro. Comparing the voices the Kokoro service offers is something
    anybody can do by ear from the speech settings; it gates nothing.

Also blocked here: **final task-model selection**, which needs live latency and
quality evaluation on the deployment GPU rather than the developer screening
checks that exist today. Source: `docs/debt-register.md`.

## 4. Blocked - deployment

Requires the installed private-LAN deployment and, where noted, a supervised
session. Implementation is published for all of these; what remains is
acceptance. The owner chose on 2026-09-02 to walk these through together in a
session - one item at a time, with what to do and what to look for - rather
than from a checklist, and to exercise preset routing on the way.

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

16. **The first Wan clip.** Download `wan2.2_ti2v_5B_fp16.safetensors`, the
    `umt5_xxl_fp8_e4m3fn_scaled` text encoder and `wan2.2_vae` into ComfyUI,
    add the video model to the catalog, install the shipped template from the
    video import card, and ask a chat for a clip. Done when the clip's journal
    shows the template's stages. Source: item 1G-4, `docs/media-catalog.md`.

## 5. Temporary by agreement

Deliberately shipped with a planned end, so it is never mistaken for a permanent
surface.

- **Remove the routing tester** once preset routing is demonstrably stable on
  the deployment. It exists to make routing-card authoring observable rather
  than guesswork; when routing is trusted it is clutter. The owner said on
  2026-09-02 that routing is still largely untested and asked for it to stay,
  with routing exercised as part of the acceptance walkthrough in section 4.
  Source: ADR 0030, `docs/settings-experience.md`.
- **Delete the OpenAI video adapter** after 2026-09-24, when its API stops
  answering. Item 1G-3.

## 6. Not advertised

Deliberately absent. Listed so none of it is mistaken for a regression, and so
no stub is ever shipped in its place. See `docs/debt-register.md`.

- Nothing about speech-to-text belongs on this list any more. Local
  transcription was here as deliberately absent; it is item 1F-5, because the
  stated goal of a fully local conversation cannot be true without it.
- Partial results that are refined as more audio arrives - the strict reading of
  streaming transcription. Not available: `wyoming-faster-whisper` transcribes on
  `audio-stop` rather than incrementally, and the OpenAI transcription API is
  request-and-response. What did ship on 2026-08-18 is cutting the recording at
  its pauses and transcribing each piece while the next is still being spoken,
  which is off by default because it does more total work. See
  [ADR 0041](docs/decisions/0041-transcribing-before-somebody-has-finished.md).
- Speech provider fallback chains. Kokoro is the voice by decision (section
  2, item 5) and there is nothing local to fall back to; a chain that fell
  back to a cloud service would contradict a conversation that stays here.
- Masking the server's outbound address (VPN or Tailscale) for optional
  internet lookups such as the CivitAI model lookup. Owner-wanted long term
  (2026-08-26). Today the honest statement is that a lookup goes out from the
  server's own address, and routing it through a tunnel is an Unraid-level
  choice; an in-app option must not ship as a checkbox that does nothing.
- Speech from a provider this deployment has not heard. Streaming and
  interruption are built against the local Kokoro path, which is the provider
  by decision; nothing else is claimed to have been evaluated.
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
Nothing is open here right now.

1. ~~After the deploy, is 8k context affordable alongside speech and image
   generation on the 12 GB card?~~ **Answered by measurement on 2026-08-18.**
   Yes, and it is free - but the answer turned out to be about the quantisation
   rather than the context.

   On a 12B at Q4_K_M the model sits entirely in VRAM and 8192 tokens runs at
   the same speed as 4096: 95.1 against 94.5 tokens per second, both fully on
   the GPU. On a 12B at Q5_K_M the model does not fit at *any* context size -
   the GPU holds a fixed share and the rest spills to the CPU, so 4096 already
   ran with 0.92 GB off-card and 8192 with 1.65 GB, dropping 54.8 tokens per
   second to 36.5.

   So the context window was never the expensive choice. Reading the 33% drop
   as the cost of 8k would have been the obvious conclusion and the wrong one;
   what it measures is a model that was already half a gigabyte too big. A Q4
   quant of the same size model is roughly 2.6x faster at 8k than a Q5, and
   leaves headroom for the image and speech services besides.

   Still untested with SDXL generating at the same moment, which is the only
   remaining part of the original question.

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
  free number is 0042.
- Run `python scripts/audit_public_repo.py` before every public commit. This
  file must never contain deployment addresses, hostnames, user-specific paths,
  hardware inventories, or account identifiers.
