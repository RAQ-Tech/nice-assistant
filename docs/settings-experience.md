# Settings experience

Nice Assistant settings are product controls, not a mirror of database fields
or provider payloads. A person who operates their own server should be able to
understand what a setting changes, whether the related feature is ready, and
what to do next without reading the source code.

## Interaction rules

- Lead each tab with its purpose in plain language.
- Keep the primary screen concise. Put short explanations behind a consistent
  information icon that appears on hover and keyboard focus; keep warnings and
  consequences that require a decision visible in the flow.
- Put the common path first. Hide provider diagnostics, thresholds, raw
  workflow controls, and destructive actions under clearly labeled advanced
  sections.
- Prefer pickers, previews, and recognizable names over opaque resource IDs.
- Show readiness as separate truthful facts. Do not collapse configured,
  reachable, generation-capable, and verified into one ambiguous status.
- Explain optional dependencies where they are used, including what the
  dependency cannot do.
- Use labels based on the operator's intent. Internal consent, capability, and
  provider terminology belongs in supporting text only when it materially
  affects privacy or behavior.
- Keep dangerous actions separate, explicit, and reversible where possible.
- Do not erase expert control; progressive disclosure should make it available
  without making it the first thing every user must understand.

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

### Persona Pictures — delivered

Visual Identity became Persona Pictures: one surface for how a persona looks,
the references that make it recognizable, and the pictures kept for reuse. It is
a rename plus a section, not a new tab, so the number of top-level settings tabs
is unchanged.

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

The identity behavior card names how the face is produced rather than offering a
choice, because only one mechanism is implemented and a control that can only
block is worse than a plain statement. The comparison threshold and what to do with a below-threshold image live in the advanced
section with the verifier they belong to: comparison measures a finished image
and cannot make generation resemble the reference.

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

### 21B — Everyday settings — delivered

General, TTS, STT, Image Generation, Video Generation, Memory, User, Personas,
and Workspaces now use the same approachable structure:

- Common choices appear first in goal-oriented cards; diagnostics, credentials,
  retention, tuning payloads, and new-persona defaults begin closed.
- A shared accessible information icon reveals concise explanations on hover or
  keyboard focus without filling the page with instructional copy.
- Speech and transcription copy describes the push-to-talk behavior that exists
  today. Speech does stream, hands-free listening is offered where it is
  configured, and transcription can run against a self-hosted Whisper service on
  this network or against OpenAI. What is still not implemented is transcribing
  while somebody is still speaking.
- Memory distinguishes pending, forget, and permanent delete, including atomic
  bulk actions. Persona editors remain collapsed until selected, and workspaces
  explain their organizational scope.
- Each persona has a closed Character card editor with definition, personality,
  style, and behavior fields. Every field shows its own token cost as it is
  typed, and a budget meter reports the total against the limit and what is left
  for conversation history. Going over the limit warns before saving rather than
  after, and the card saves through its own action, separate from Save persona.
- Card and lorebook authoring guidance, including a complete worked
  example, is in `docs/persona-authoring.md`.
- Each persona also has a closed Lorebook. Entries are collapsed by name — the
  same convention Media Catalog and Task Models use — and each summarizes what it
  fires on, whether it is always included, and whether it is switched off. A
  preview box takes a pasted message and reports which entries fire, which fit the
  allowance, and which were left out, so keyword tuning is observed rather than
  guessed. Entries load only when the section is opened.
- User adds a short About you profile sent with every message. It is refused when
  saved if it would not fit, naming the budget, because it is never dropped to make
  room once stored. The display name is sent with it, so that setting now changes
  runtime behavior instead of only labeling the browser.
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
- Each persona editor exposes `Allow persona to send images`, defaulting on.
  Turning it off withholds conversational picture fulfillment for that persona
  without disabling direct user image actions or authorizing unsolicited work.
- Persona avatars and Visual Identity thumbnails open the same in-app
  full-image viewer used by chat pictures; they never launch a separate browser
  window.
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

### 21C — Operator settings — delivered

Models, Task Models, Media Catalog, GPU Coordination, and Data retain their
operator controls behind a consistent guided structure:

- Each tab leads with its actual purpose and separate readiness facts rather
  than presenting configuration as proof of health.
- Models shows the effective default, installed Ollama count, context window,
  and saved per-model customization count. Sampling controls and the real
  per-model override editor begin closed.
- Task roles and media resources are collapsed named editors. Budgets, failure
  policy, raw provider payloads, and content-free audit records remain
  available under nested advanced disclosure.
- Media compatibility is selected by named base model instead of requiring
  operators to copy internal IDs. Catalog drafts, planning limits, and
  deterministic plan previews remain explicit.
- GPU Coordination separates measured capacity, adapter capability, and
  operator authorization. Managed-mode consequences remain visible because
  they affect external provider state.
- Data separates backup creation from restore verification and destructive
  archive deletion. Permanent deletion uses an explicit consequence warning.
- Tabs with independent persistence no longer display a global save button
  that cannot save their changes; each operation has a local action instead.

The operator logic is split into focused typed modules so the settings shell no
longer owns Task Model, media-catalog, coordination, or backup workflows.

These chunks were intentionally separate. Visual Identity needed a new
protected media-list contract and an interaction redesign; the everyday and
operator tabs have different audiences and therefore use separate modules and
interaction depth.

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
