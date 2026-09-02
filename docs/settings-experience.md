# Settings experience

Nice Assistant settings are product controls, not a mirror of database fields
or provider payloads. A person who operates their own server should be able to
understand what a setting changes, whether the related feature is ready, and
what to do next without reading the source code.

## Interaction rules

The design language, from the owner's 2026-08-26 framing and the model page
that proved it:

- A list of plain things, each opening a page of its own. A persona, a
  conversation model, a background role, a catalog model: the group page is
  the list, and the thing's page has its name as the headline, arrows to its
  neighbours, and an address (`#/settings/Personas/<id>`) so it can be linked
  to and returned to.
- Sparse fields, labelled plainly. Help waits on hover - a `title` on the
  row, read by assistive technology as the control's description - and never
  takes up room. A page says at most one thing out loud.
- Never an information icon explaining an explanation. Image Generation,
  Video Generation, Media Catalog and Persona Pictures still carry the earlier
  icon-per-row shape until they are redone in the same pass as the rest of
  Pictures; nothing new is built that way.
- Change the input type before adding words: a dropdown of what the provider
  actually reports beats a typed box, and a prefilled value says where it
  came from.
- The rest goes behind one "More options" fold per page, closed by default.
  Expert control is never removed, only put away.
- Leaving a page with unsaved changes asks: stay, leave without saving, or
  save and continue. The safe answer is the default, and the section
  navigation asks the same question a page's own buttons do.
- Readiness stays separate truthful facts, and a warning that changes
  another service's state stays visible in the flow.
- Dangerous actions are separate, explicit, and reversible where possible.
- Every control that offers a provider says which kind it is in the option
  itself.

## Local and cloud

Both are offered, everywhere they make sense. This runs on somebody's own
machine, and plenty of people running it will want a cloud transcription or a
cloud image model because their hardware cannot do better. Removing that choice
would be deciding for them.

Three rules make the choice safe rather than removing it. Every default is local
or off. Nothing escalates from a local provider to a cloud one without somebody
having paired them. And every control that offers a provider says which kind it
is, in the option itself - "leaves this machine" rather than a warning paragraph
somebody has already scrolled past.

A provider nobody has classified reads as unknown, not as local. A privacy
claim should fail closed, and an unknown part stops the page claiming everything
stays here.

The homepage carries the summary: one line per part of a conversation, saying
where it goes right now. It is there so the answer to "is this private" is a
thing you can look at rather than a thing you have to reconstruct from four
settings pages.

## Delivery chunks

### 21A — Visual Identity — delivered

- Guide the operator through selecting a persona, enabling private reference
  storage, choosing an image, and explicitly approving it.
- Replace protected-media ID entry with an owner-scoped generated-image
  thumbnail picker.
- Separate reference storage, reference-aware generation, the editable
  no-workflow fallback, optional comparison, and measured comparison-failure
  behavior into honest readiness rows and controls.
- Explain that CompreFace is an optional comparison service. It can evaluate a
  generated face but cannot make generation resemble the reference.
- Use fictional-persona language for the rights confirmation while preserving
  the durable backend consent and audit model.
- Keep verifier settings, thresholds, manual validation, history, and deletion
  in an optional advanced section.

### Persona Pictures - the face, the recipes, the kept pictures

Persona Pictures is one surface for a persona's appearance. It leads with the
same face card the persona's own page carries (see the Personas page below):
one switch, "Looks like this photo", and the photos, with a line only when
something needs a hand. Beneath it are the preferred recipes and the kept
pictures, and under one fold what comparison needs: the optional verifier, the
outcome policy and threshold, manual comparison, and the activity history.

Kept pictures show the description they were stored under, because that
description is what a later request is matched against; an entry nobody can
interpret is one nobody can decide to remove. The action is "Forget", and it
says the picture itself stays - removing a library entry stops reuse, it does
not delete an image. A retired entry explains that it is past the keep limit
rather than appearing broken.

Preferred recipes sit above the kept pictures: which presets are known to work
for this persona, best first. Routing prefers them when a request does not call
for something else, and a preference that names a preset which can no longer run
is ignored rather than blocking the picture. A preference is persona-specific
knowledge that a routing score cannot represent, which is why it is recorded
rather than inferred.

The face names how it is produced rather than offering a choice, unless the
catalog can apply more than one mechanism, because a control that can only
block is worse than a plain statement. The comparison threshold and what to do
with a below-threshold image live in the fold with the verifier they belong to:
comparison measures a finished image and cannot make generation resemble the
reference.

### Media Catalog is preset-first — delivered

A preset is the thing an operator opens, so presets lead the screen and the
individual models, LoRAs, and workflows they are built from sit behind an
Inventory disclosure. Most work happens in a preset; the parts are where you go
when something is missing.

Every value that decides how a picture comes out has a named field: prompt
style, prefix and suffix, whether the model takes a negative prompt at all,
where LoRA trigger words go, steps, guidance, sampler, scheduler, and permitted
dimensions. A model that takes no negative prompt says so where the field would
have been, including that the platform safety negative cannot be carried either.
The raw definition stays reachable under disclosure for the cases named fields
do not cover, but it is no longer the only way in.

A preset with no routing card says so in its own summary, because routing reads
that sentence and an empty one silently reduces the preset to tags and priority.

### Routing tester — delivered, temporary

Media Catalog carries a routing tester behind advanced disclosure. It exists so
preset routing cards can be observed rather than guessed, in the same spirit as
the lorebook preview. It is labeled in the product as a diagnostic expected to
be removed once routing is stable, and it must not become a permanent surface:
if it is still there when routing is trusted, it is clutter.

### The pages, one thing each - delivered 2026-09-02

Every group outside Pictures now has the model-page shape. Navigation is
unchanged: five groups named for intent - Conversation, Voice, Pictures,
Personas, System - a search box that matches the words a person actually
thinks, and section ids that still answer every deep link. A thing inside a
section has an address of its own, `#/settings/<section>/<item>`.

Conversation:

- **General** is Theme, Model, whether replies are spoken, and the
  visualizer. Technical messages, model thinking and signing out after
  inactivity are folded.
- **Models** is the list Ollama reports, the default marked, with the shared
  defaults under it - default model, temperature, reply length, context
  window - and sampling folded. Each model's page carries the same numbers
  prefilled from the defaults and says so; the first change customizes that
  model alone, a switch makes it the default for new chats, and one action
  returns it to the shared defaults. Everything still lives in the one
  settings object the header's Save writes.
- **Memory** is the mode for new chats, one line saying that only approved
  memories reach a conversation and what forget and delete each mean, then
  the pending, active and history groups with their atomic bulk actions.

Voice:

- **Spoken replies** is who speaks - Off, a local service on this machine,
  or OpenAI, which leaves it - then the fields that provider actually reads,
  a connection check, and the stored format and voice direction folded.
- **Transcription** is who transcribes, the language, and for a local service
  which of its two shapes it is: OpenAI-compatible (speaches, whisper.cpp,
  LocalAI) or Wyoming, the one Home Assistant voice already runs. Hands-free
  listening and transcribing at natural pauses appear once something can
  transcribe; keeping recordings is folded.

Personas:

- **Personas** is the people, each a chip with their picture or initials, and
  one page each: picture and name as the headline, model, whether the persona
  may send pictures, which workspaces it is available in (only when there is
  more than one), the face - one switch, "Looks like this photo", and the
  photos, with a line only when something needs a hand: add a photo, or
  install a workflow that can use one - then the Character card and Lorebook
  editors - each still
  with its own save, its cost meter and its preview - and free-form
  instructions and deletion folded. The page saves itself; leaving with
  unsaved changes asks. Instructions for a new persona sit under the list.
- **Workspaces** is the default workspace, one line on what a workspace is,
  and each workspace with its name editable in place.
- **Your profile** is name, About you with the one visible reminder to keep it
  short, timezone, and the OpenAI key folded.

System:

- **Background models** is the roles - chat titles, summaries, memory
  proposals, capability planning - each a chip saying which model it uses or
  that it is off, one line on what background work is, and the recent runs
  folded. A role's page states what the role does under its name, then On,
  Model, Fallback model, budgets and failure behavior folded, and Save role
  beside Check readiness with its result.
- **GPU Coordination** is the mode, its save, timing and reserve folded, the
  one line that unknown capacity is never presented as free VRAM, and each
  provider endpoint as a row that opens to what it reports and two switches:
  nothing else uses it, and only then, allow releasing its models. The
  managed-mode warning stays in the flow because it changes another
  service's state.
- **Data** is the backup and diagnostic actions, one line to verify an archive
  before relying on it, and each archive as a row with download, verify and
  delete.

What did not change: the character card cap and cost meter, lore copying,
the memory distinctions, the persona image permission, the in-app avatar
viewer, and every provider control naming which kind it is. Authoring
guidance for cards and lorebooks stays in `docs/persona-authoring.md`.

### The pictures pages - earlier shape

Image Generation and Video Generation keep the goal-oriented cards with
information icons, readiness facts and closed advanced sections, and are redone
with the rest of Pictures:

- Local image connection choices remain readily available while sampling,
  authentication, and raw JSON controls live under advanced disclosure.
- Enabling an image provider seeds a starter Media Catalog model only when no
  image resources exist, so conversational planning becomes available without
  overwriting an operator-curated catalog. Legacy local-provider aliases are
  shown and saved in their canonical `local` plus backend form.
- Media choices the deployment cannot honor are refused when they are saved,
  naming the accepted values, instead of being stored and then quietly replaced
  with a default during generation. An account holding an unusable value from
  before that check keeps it and can still save its other settings.
- Image Generation exposes the same persisted blur preference available in chat
  controls. Its readiness summary separates provider reachability, basic
  generation, and optional identity enhancement so missing identity setup never
  reads as a basic block. Redundant per-image approval is not an everyday
  setting.
- The chat control popover keeps speech, `Blur images`, and `Stop audio` in the
  common path. Workspace, model, memory mode, client state, and visualization are
  preserved under `Chat details`; they are not removed or made read-only.

Provider tuning still controls direct actions. Media Catalog remains the
operator source of truth for planned conversational generation. Technical plans
and rejected-resource reasons are collapsed Details rather than default chat
content; failed planned requests retain a focused retry or correct identity
remediation. Visual Identity keeps both runtime policies editable: whether to
generate with a warning while conditioning is unavailable, and what to do after
a real comparison failure. The Media Catalog setup imports API-format workflow
JSON, reports missing ComfyUI nodes/assets, and creates an explicit reference
binding without claiming that schema inspection is a live generation test.
Media compatibility is selected by named base model instead of requiring
operators to copy internal IDs; catalog drafts, planning limits, and
deterministic plan previews remain explicit.

## What has happened to the pictures

Media Catalog ends with the counts recorded against each preset: pictures kept,
sent again, and removed, with the score they produce. It sits after the presets
rather than before them, because it describes them.

Shown with the counts rather than as a score alone. A score of one from a single
signal is not the same as a score of one from twenty, and an operator deciding
whether to trust it needs both numbers. Each preset's counts reset on their own.

## Exporting a preset

Each preset editor has an Export action. It does not write a file: it shows what
a file would contain, field by field, together with what is deliberately left
out and anything the recipe needs that a file cannot carry. The file is written
only when the operator saves it from that preview.

## Importing a preset

Media Catalog accepts a preset file. Choosing one shows what it would do here -
which recipes install, which cannot and why, and what each still needs - above
a confirm action that is disabled unless the whole file can be installed. The
warnings sit above the list rather than below it, because deciding whether to
run somebody else's graph comes before deciding whether the recipe is any good.

## Getting back out

The mark at the top left of a chat returns to the homepage. It sits before the
drawer toggle, sized to the icon buttons beside it, and the browser journeys
assert at 375px that the settings and log-out controls are still visible with it
in place - the point being that it did not push anything off the header.

## Quick settings on the homepage

Three settings sit on the front page: whether pictures are made overnight and
between which hours, whether replies are spoken, and whether saved memory is
used in new chats.

Background production is there for a reason. It spends real electricity on a
schedule while nobody is watching, and a setting nobody sees is a setting nobody
revisits. Beside the control it says whether the current hour is inside the
window, how many approved scenes are waiting, why production is not running
right now in the platform's own words, and what it last made.

There is no second copy of any of these values. Every control reads and writes
the same settings object the settings page edits, which is the same one sent to
`PUT /settings`, so the two surfaces cannot drift apart - there is nothing to
drift. When a deployment forbids background production the switch is disabled
and says so, rather than moving and being ignored.
