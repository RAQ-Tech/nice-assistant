# Media model catalog and coordinator

The media catalog is the operator-owned source of truth for image and video
resource fitness. It describes exact provider resources; it never infers
capability from a checkpoint, LoRA, or workflow filename.

## Getting models in

The catalog only knows the models an operator has told it about, and for a long
time telling it meant typing a checkpoint filename exactly right into a bare
dialog. On the deployment this was built against, one model was cataloged while
forty-five checkpoints sat installed in ComfyUI - so every picture ran through
one model and one recipe, and the owner noticed the sameness long before anyone
found the cause.

Discovery closes that gap. The settings page asks ComfyUI for its installed
checkpoints - the same `/object_info` answer inspection already reads, where the
checkpoint loader's option list is the list of files - marks which are already
cataloged, and adds the ticked ones by exact filename. An added model arrives
enabled with permissive content tags (one operator's private deployment; narrow
a model's tags when it should not serve some content), and the lazy preset pass
gives each one a recipe, so adding models is directly adding the variety the
planner can offer.

A workflow can be pasted in the same spirit: the page states the accepted format
out loud - a ComfyUI export in API format - and the graph is inspected by
ComfyUI before it is saved, with the prompt binding taken from the inspection's
own candidates rather than guessed. Inspection distinguishes roles: an identity
graph must route a reference image through an identity node to an output, while
a general graph only needs somewhere for the request prompt to land.

Installing a shipped template twice deliberately creates a second copy - a copy
can be tuned separately - but it is now a choice rather than an accident: the
card shows the installed state and copy count, and installing another copy asks
first. Five identical InstantID graphs were once created by five hopeful clicks
on a button that never said it had already worked.

## The model page

The owner's framing, adopted as the design language: a model is an ingredient,
a workflow is what you do with it. Day-to-day settings therefore live with the
ingredient. Each model's name in the catalog opens a page for that one model:
its nickname as the headline (the exact filename stays on hover and under the
name), a "Show in Nice Assistant" switch, the plain-language note routing reads
when choosing between models, and the settings the model likes - steps,
guidance, sampler, scheduler, size. Arrows walk to the previous or next model;
leaving with unsaved changes asks whether to save, discard, or stay, and the
safe answer is the default.

Under the hood the page edits the model resource and the model's own recipe
together - the recipe machinery is unchanged, and the raw recipe list remains
in Operator tools for multi-recipe and diagnostic work. Two honesty rules
shape the page:

- Sampler and scheduler are dropdowns of what ComfyUI actually reports
  installed (from the same `/object_info` answer discovery uses), and fall
  back to typing boxes only when ComfyUI cannot be asked.
- Suggested settings carry their provenance. A safetensors file can carry a
  metadata block naming its architecture; ComfyUI serves it via
  `/view_metadata/checkpoints`, and when it names the family the suggestion
  says "read from the file". When only the filename hints at the family the
  suggestion says it is a guess. When neither says anything, nothing is
  suggested. The family table lives in `app/model_prefill.py` and nowhere
  else; suggestions apply only when a person presses Apply.

The page also offers the one catalog action that leaves the LAN: a CivitAI
lookup. It never runs as a side effect - a person presses the button, and a
popup names civitai.com and offers cancel, ok, and "don't show this again"
(remembered through the ordinary settings save). The app cannot hash a model
file it cannot read, so the search runs on the filename and returns a
pick-list rather than an auto-fill; an exact filename match is marked. A
picked match fills the form for review with the model's proper name, its
trigger words as a prompt prefix, and the settings most common across the
creator's own showcase images, with A1111 sampler names translated to
ComfyUI's vocabulary (`app/civitai_lookup.py`). Nothing is saved until the
person saves.

## The model's face

Every model can carry one sample picture, shown beside its name on its page
and on its chip in the models list - a look you can see instead of a
filename. "Make a sample" renders one picture of a fixed neutral scene with
exactly the settings on the page (the direct image job accepts per-call
steps, guidance, sampler, and scheduler for this reason), then pins the
result as the model's `sample_media_id`. The scene is fixed so thumbnails
compare looks rather than subjects; failure states say plainly that ComfyUI
could not be reached rather than spinning.

## Steering from the chat

Changing what a picture looks like must not mean a trip to settings, so every
finished picture in a conversation carries two quiet buttons. "Another take"
re-runs the request with the recipe that made it pinned - pinned, not
preferred, because a button with that name that silently switched recipes
would be a lie. "Different look" sets that recipe aside so routing must
choose another, and when there is no other enabled recipe it refuses plainly
and names the way out ("add another model") rather than re-rendering the
same look. Both re-plan under every ordinary rule - content tags, features,
capacity - and both land in the chat as a new picture beside the old one,
with the same journal a planned picture gets. The planner carries the two
hints as `pin_preset_id` and `exclude_preset_ids` in plan requirements.

## Local video

Video is local-only by decision (2026-08-26). OpenAI's Sora API - the only
cloud video path this product ever had - shuts down on 2026-09-24, and every
surviving cloud video service both moderates away this product's content and
would receive persona reference faces to do anything persona-shaped. The
OpenAI video adapter remains in the code, unreachable from the UI; the
settings page offers Off and Local, and a stored cloud choice renders and
saves as Off.

Local video is the image machinery with a different output: a video model
(provider `local-video`, backend ComfyUI - Wan and its relatives load as
UNETs, and `unet_name` binds exactly as `ckpt_name` does) pairs with a video
workflow in a recipe, the planner selects it for `kind: video` requests, and
execution submits the bound graph to the same ComfyUI address the image path
uses. Two honest differences: the history poll budget is thirty minutes
rather than two, because a clip renders for minutes; and there is no fallback
graph - a video request without a cataloged workflow is refused in plain
words rather than rendered through a graph nobody chose. Outputs are
collected from every collection ComfyUI reports (`videos`, `gifs`, `images`)
and a real container wins over an animated fallback.

Workflows arrive through the same import card as image workflows - a "This
workflow makes" choice sets the kind - and for video the card leads with the
shipped Wan 2.2 template: a text-to-video graph for the 5B model, its node IDs
fixed and its prompt, negative, seed and model bindings declared by
construction. Its node names come from ComfyUI's own source, where the Wan 2.2
and video nodes live, and checking it asks the live `/object_info` whether
they and the three files it names - the model, the text encoder and the VAE -
are installed, offering the files ComfyUI does have where one is missing. The
graph carries its size, 1280 by 704 and 121 frames at 24 frames a second,
because the model was trained at it, so the request's picture size is not
bound into it. Nothing here has rendered a clip on the deployment; the first
one is the live test, and it is tracked in `BACKLOG.md`.

## Resource metadata

Each owner may register models, LoRAs, and ComfyUI workflows with:

- image or video kind, provider/backend, exact external identifier, enabled
  state, and deterministic priority;
- supported operations, domains, content tags, and required features;
- operator-estimated VRAM and load time;
- validated provider defaults and operator notes; and
- explicit add-on-to-base-model compatibility edges.

For a ComfyUI workflow resource, the external ID is a stable catalog identifier;
the executable content is a required, non-empty inline `workflow_patch`. Nice
Assistant does not pretend ComfyUI can load a named workflow through an API that
does not provide that behavior.

An identity workflow additionally declares the `identity_control` feature and
non-empty `identity_image_bindings` in its default settings. Every binding is an
exact `{node_id, input_name}` that must already exist in the inline API-format
workflow patch. Nice Assistant uploads the reviewed normalized reference through
ComfyUI `/upload/image` and replaces only those declared inputs. This supports
operator-tested IPAdapter, InstantID, PuLID, or other graphs without pretending
their custom-node schemas are interchangeable.

The browser provides a focused Identity Control setup card. Operators import an
API-format graph, inspect it against the configured ComfyUI `/object_info`, select
a detected reference-image input and compatible base model, then save the exact
graph and binding. Missing custom nodes or selected assets are reported by name.
The inspection enables saving only when provider metadata proves complete
required inputs, valid typed links, an acyclic path to an output, and a path from
the selected reference input through a recognized identity application node.
Graphs that cannot be proven remain drafts. This structural proof is not a
successful live generation or an identity-match result. The expert resource
editor remains available for deliberate manual changes. An enabled workflow must
have a non-empty patch; an enabled
`identity_control` workflow must also have at least one valid binding.

## Generation presets

A preset is the tested recipe: which checkpoint, which workflow graph, which
LoRAs at which weights, the sampler settings, the permitted dimensions, and the
prompt dialect that combination was tuned with. It also carries a routing card -
plain language written by the operator saying when the preset should be chosen -
and the semantic metadata the platform hard-filters on.

Every resource a preset names must exist, match its kind, and already be marked
compatible with its base model. A preset is meant to describe a combination
someone has run; letting it point at a LoRA the catalog never paired with its
checkpoint would recreate exactly the untested-combination problem presets exist
to remove.

Automatic LoRA selection survives only where a preset declares an open slot. A
slot names the one axis the operator is willing to let vary and how far, and the
existing explicit compatibility edges still gate what can fill it. Everything
else about a preset is a fixed, tested choice.

A preset declares which identity mechanisms it implements. A persona image is
planned only against a preset that can honor the mechanism the persona's
Identity Spec requires, and one that cannot is rejected with a reason naming the
mechanism rather than silently producing an unconditioned picture.

Nothing is inferred from intent: a mechanism is either declared by the operator
on the preset, or proved by the graph the plan actually selects. A workflow
proves `reference_adapter` by declaring the `identity_control` feature and
naming where the reference goes, because only the binding makes the reference
reach the graph. Either is enough, which keeps a preset from being refused for a
capability its attached workflow plainly has, and keeps a stored value from
going stale when a workflow is added later.

A preset created automatically from an existing catalog model declares a
mechanism only when the catalog can already supply it. Claiming one for every
model made the filter that exists to reject an incapable preset reject nothing,
and put a capability in the record with no wiring behind it.

The existing ADR 0018 fallback is unchanged. When conditioning cannot be
completed and the saved policy allows it, the request falls back to an
explicitly unconditioned picture, and the mechanism requirement is dropped with
the feature it belonged to.

Presets are evaluated with their later passes included. A capability that only a
second pass provides is still a capability the recipe has, so a preset that
generates the scene and then applies the face covers `identity_control` even
though its first graph does not. Each pass carries its own bindings: they are
assigned per pass, never merged, because a binding a pass does not declare must
not stay pointing at the previous graph's node IDs. The approved reference goes
to the pass whose graph actually has the nodes for it.

A preset may also declare an open workflow slot. That lets it reach a
feature-capable graph it does not name itself, which is how identity
conditioning is applied today. A graph filling that slot must declare the
operation being requested. Covering a wanted feature is not a reason to run a
graph that cannot do the job: an image-to-image identity workflow attached to a
generate request has no source picture, so it used to be selected and then fail
at upload time. It is off by default, because a preset is meant
to be a fixed recipe; presets created from an existing catalog model turn it on,
since attaching a feature-capable workflow at request time is exactly what the
coordinator did before presets existed.

Presets are single-pass unless they declare stages. A preset with declared
stages runs them in order, each pass receiving the previous pass's picture, so
"generate the scene, then apply identity to the result" is a property of the
recipe rather than something that only happens after a failed measurement.

Every stage after the first must name a workflow with a real source image
binding, because it is handed a picture. A stage that cannot accept one is
refused when the preset is saved, not discovered mid-generation. Each stage
records its own journal entry.

Intermediate passes are working state, not pictures the owner asked for. They
are written to a scratch file and removed, so the library holds only the final
result.

Sequential stages never coexist, so a plan is costed as the resident base and
LoRAs plus the single most expensive stage, not the sum of every stage. That is
the ADR 0013 rule, now applied to declared stages as well as identity
correction.

Every enabled base model is given a preset once, carrying that model's dialect,
sampler settings, size, and priority, plus one open LoRA slot sized to the
catalog's LoRA limit. That reproduces what the coordinator does today, so
nothing an operator has configured changes meaning. The backfill is lazy per
owner, the same way legacy provider settings are imported, so an account set up
after the migration is treated identically without a second migration.

The API is `/api/v1/media-catalog/presets` and
`/api/v1/media-catalog/presets/{id}`.

## Planning context

Capability planning receives a bounded window of this chat's earlier user
messages, oldest first, so a request that refers to something already
established can be routed and described correctly. The window is bounded in both
message count and total characters, and the newest messages survive when the
allowance runs out, because those are the ones a request is most likely to refer
to.

Persona reply prose is still excluded. That exclusion is the reason ADR 0017
exists - it stops a persona inventing or widening a media subject - and widening
the window over the user's own words does not weaken it. The current request
remains authoritative: earlier messages may complete a request but never make a
capability appropriate on their own. The window that informed a picture is
recorded in that picture's journal and in its capability request.

## Routing a request to a preset

The platform hard-filters what is legal for a request, then offers the Task
Model a bounded shortlist of enabled presets as opaque labels with each one's
title and routing card. The routing card is the operator's own words about when
that preset applies, and it is the only reason the model has to prefer one over
another. No provider, model, LoRA, workflow, filename, or setting appears in the
shortlist, and the model can only return a label the platform offered.

The shortlist is a coarse pre-filter: the request's operation and required
features are not known until the model answers. The full hard filter still runs
at plan time, and it can reject the model's choice - a preset that cannot serve
the request is not used because the model liked its description. When that
happens the plan carries a visible warning.

A persona can record which recipes are known to work for it, best first. That
preference is consulted only after the task model's own choice, and only among
presets that already passed the hard filter: the model saw this request, while a
preference is standing knowledge. A preference naming a preset that no longer
fits is skipped rather than blocking, so a stale entry never stops a picture
being made.

Selection falls back to the deterministic score whenever the model expresses no
preference, the persona has none that fits, or the model fails, times out, or
returns something unusable. The fallback picks
from the same candidates the model was choosing between, so a planning outage
degrades the choice rather than the result.

The plan records which preset won, whether the model, a persona preference, or
the deterministic score chose it, and what else was considered. All of it reaches the generation
journal.

### Starter presets and the bundle format

A preset bundle names assets the way a person does - by the filename the
provider reports - rather than by this installation's resource IDs, which mean
nothing anywhere else. That is what lets the same format carry the built-in
starters now and shared presets later.

The shipped starters carry published defaults for common model families: the
sampler, step count, guidance, dimensions, and prompt dialect that family
expects. They are a starting point, not a measurement. Nothing in them has been
tested on this deployment, and the product says so wherever they are offered.

Installing resolves each starter's named assets against the owner's catalog. A
starter whose model file is not there is reported with the filename it wants,
rather than installed as a preset that could never run - the operator learns
what to install instead of meeting a failure during generation. A starter whose
name already exists is skipped, never overwritten, following the same
missing-kind rule as ADR 0016: operator-curated configuration is not replaced by
a bootstrap.

The API is `/api/v1/media-catalog/starter-presets` and
`/api/v1/media-catalog/starter-presets/install`.

### Routing tester

Settings -> Media Catalog has a routing tester under advanced disclosure. Paste
a message and it reports the shortlist that would be offered, whether an image
would be requested at all, which preset routing chose, whether the task model or
the deterministic score chose it, and any plan warnings.

It runs the real shortlist, the real Task Model role, and the real planner, so
what it shows is what would happen rather than a simulation. Nothing is
generated. A task model that fell back to its configured policy is reported as
such, because "no image was requested" and "routing never ran" need different
fixes.

This is deliberately temporary tooling. Authoring a routing card is otherwise
guesswork - there is no way to see whether the sentence you wrote makes the
preset you meant win. It is expected to be removed once routing is demonstrably
stable.

## The scene backlog

Pictures that have been proposed for a persona but not made. It is kept separate
from the retained library because "we could make this" and "we have this" are
different facts, and one record holding both would make a plan look like an
achievement.

Every entry carries where the idea came from. A proposal nobody can trace back
to what suggested it can only be accepted or deleted on instinct, which is not a
review.

An operator moves an entry between proposed, approved, and retired. `generating`
and `done` describe work rather than intent, so they are not offered as
something to click - a state that claims progress nobody made is the kind of
modelled state `AGENTS.md` rules out. A retired entry can be reconsidered, which
returns it to proposed rather than jumping to approved.

Scenes can be proposed automatically from what a persona already is: its card,
its lorebook titles, and what its recent conversations were about. A dedicated
Task Model role does this. Every proposal must say which of those suggested it
and quote the detail it drew on, so a person can judge the idea rather than
guess at it, and the model is given what has already been proposed so it does
not restate the backlog.

Chat titles are used as the conversation signal because they are already a
short, generated summary. Re-summarising message bodies for this would put
private conversation content in a second place for no extra signal.

Proposals arrive as `proposed` and are never approved automatically, so nothing
reaches generation without a person agreeing to it. A response says whether the
model answered, because a fallback and "no ideas" both come back empty and need
different fixes.

### When background pictures may be made

Pre-generation spends real electricity on a machine somebody is using, so the
decision to start one is a small, pure, tested function rather than a scattered
set of conditions. A background picture may start only when production is
switched on, the hour is inside the configured quiet window, no conversation is
waiting, no requested picture is queued or running, and an approved scene is
waiting.

Every refusal carries a reason. "Nothing happened last night" and "it is broken"
need different fixes, and an operator cannot tell them apart without being told
which it was, so `/api/v1/scene-backlog/production-readiness` reports the reason
whether or not anything could run.

It is off by default, and it is an owner setting rather than deployment
configuration: the switch, the quiet window, and the per-run cap are stored with
the account and read on every pass. The environment supplies what a new account
starts with, and keeps one veto - a deployment with production switched off
cannot have it switched back on from a browser.

A window whose start and end are the same hour is refused when it is saved. It
would produce a switch that is on, a schedule that looks set, and a feature that
never runs, which is worse than either honest state.

### Exporting a preset

A preset in the catalog is written in this installation's resource identifiers,
which mean nothing anywhere else. Export rewrites it in the names a person would
recognise - the filenames the provider reports - and carries nothing measured
here.

Deliberately not in the file: VRAM estimates, which were measured on this
machine; provider addresses and local paths; this installation's resource
identifiers; and workflow graphs, which contain this installation's node
numbering.

A workflow is therefore named as a requirement rather than dropped. So is an
identity mechanism, a per-pass workflow in a multi-pass preset, and any asset
this installation could not name. A recipe that arrives missing a piece it never
mentioned is worse than one that arrives asking for it.

The preview shows every field that will leave, one row each, before anything is
written. The export is round-tripped through the same validation an imported
file faces, so an export that could not be imported fails here rather than on
somebody else's machine.

### Importing a preset

A file names its assets by filename, so import matches them against what is
installed here. Choosing a file shows what it would do before it does anything:
which presets would install, which cannot and why, and anything a recipe needs
that a file could not carry.

All or nothing. If any preset in a file cannot be installed, none of it is, and
the reasons are named. A partly imported file leaves a catalog nobody can reason
about - some recipes present, some absent, no record of which - and refusing
lets an operator fix what is missing and try the same file again.

Every import says the recipes were tested on somebody else's installation and
have not been run here. A file containing a preset with a workflow slot says
plainly that importing it means running a graph somebody else wrote on this
machine.

A VRAM figure in a file is accepted and dropped rather than refused: it is a
measurement of the machine that measured it. The imported preset carries this
installation's estimate for its own model instead.

Requirements a file could not carry are written into the imported preset's notes
rather than discarded, so the reason it behaves differently is attached to the
thing behaving differently.

No discovery, no ratings, no registry. This is a file an operator moves
deliberately.

### What happened to the pictures

Three things are counted against the preset that produced a picture: the picture
was deliberately kept, the picture was sent again into another conversation, or
the picture was removed. Keeping and reusing earn a point; removing loses one.
That is the whole model, deliberately, because the number is shown in settings
beside the counts it came from and has to be explainable by looking at it.

Generating a picture is not counted. The platform chose the preset, so counting
that would be the platform scoring its own homework.

The counts only ever reorder presets that already passed every hard requirement.
Selection order is: the task model's choice for this request, then an
operator-set persona preference, then these counts, then the deterministic
score. Nothing here can make an incompatible preset eligible, and a preset whose
pictures keep being removed is not promoted for having been used a lot.

Each preset's counts are visible and individually resettable under Media
Catalog. Nothing in the product describes this as learning, because nothing here
learns: it counts.

### Choosing between pictures that already qualify

A persona replies before anything decides which picture is attached, so it can
describe walking the dog while a beach photo arrives beside it. Planning the
picture first was rejected on latency grounds; see A12 in the decision log.

Instead, when a request is answered from the retained library, what the persona
has recently said reorders the candidates. It cannot change which candidates
there are: the list is built from the user's own words exactly as before, with
the same match threshold and the same never-twice-in-one-conversation rule.

It reads the last three assistant replies in that chat, not the reply on the
turn making the request. A message that asks for a picture passes the
image-action gate, and ADR 0021 replaces persona prose with a neutral
acknowledgement whenever it does, so there is nothing in that reply to read. The
words that matter came earlier.

With no chat - a direct action, a background picture, a photo set frame - every
affinity is zero and the order is the one it always was. See ADR 0033, and
ADR 0017 for the rule it deliberately does not weaken.

### Direct actions and capacity

A direct image action submits the settings an operator chose. The coordinator
does not select for it, and the plan says so. What it does now carry is a
demand: the model named in the request is matched exactly against the catalog,
and that resource's recorded estimate becomes the plan's estimate.

That matters because unknown demand cannot pass measured-capacity admission at
all - a plan with a zero estimate is admitted immediately, which is how direct
actions used to slip past the check every conversational request goes through.

Matched exactly, never approximately. A near-match would attach one model's
measurement to another, and the coordinator would then enforce that number. When
the catalog has never seen the model the estimate stays unknown and the plan
says which model to add to fix it, because an invented estimate is worse than an
admitted gap.

### Photo sets

One idea, several frames. The shared scene belongs to the set and the pose
belongs to the frame: a frame may change action, framing, camera, and mood, and
nothing else. Wardrobe, setting, subject, and lighting are stored once, so they
cannot drift between frames the way they do when the same idea is described
several times and generated separately. A variation that names a field it does
not own has that field dropped rather than the whole frame refused - the useful
part of such a request is almost always the pose.

The seed relationship is recorded rather than incidental. A set picks one base
seed when it is created, and frame `n` uses `base_seed + n`. Two numbers
reproduce the whole set, and a frame that needs remaking comes back as the same
picture.

Frames are produced exactly like background pictures: chat-less capability
requests queued as bulk work, so a picture somebody actually asked for is always
chosen first. Each frame's journal records the set it belongs to, its index, and
its seed.

A frame is never answered from the retained library. Frames of one set match
each other strongly by design, so serving would quietly return the previous
frame instead of making the next one, and the set would be short a picture it
believed it had.

A set says what it actually is. `planned`, `generating`, `done`, and `partial`
are distinct, and `partial` exists because a set that made four of six frames is
neither finished nor still working, and calling it either one is something
somebody would act on.

### Sending a set into a conversation

When a request matches a retained frame that belongs to a photo set, the reply
carries that frame and the set's other frames this conversation has not already
seen. The attachment keeps its own `media_id` as the frame shown first, and the
rest arrive as `frames` beside it, so every reader that expects one picture
still gets one.

Bounded, and stated rather than implied: `MEDIA_SET_FRAMES_PER_REPLY` defaults
to three frames including the first. A set of twelve arriving at once is a wall
of pictures, not an answer.

The same rule the single-picture library already uses applies per frame: a
conversation never receives a frame it has already been sent, and never receives
one it asked to have made. A set that is only partly generated is served from
exactly like a finished one, because the query is over the frames that exist
rather than over what the set intended.

### How a background picture is made

An approved scene is produced through the same capability request, execution
plan, generation journal, and job that a conversational picture uses. It was
tempting to write straight to the library and skip all of that, but a background
picture would then be the only picture in the product with no record of how it
was made, and the record is the point.

The differences are exactly two. It has no chat, so no assistant message and no
attachment are created; the request is chat-less. And it queues as `bulk` work,
so within the media lane a picture somebody actually asked for is always chosen
before it.

The scene entry follows the work: `approved` to `generating` when the job is
submitted, then `done` with its picture. A failure, a cancellation, or a restart
returns it to `approved`. Nothing is left claiming to be in production when it
is not - a background picture is not resumable, because the job making it is
gone, so the honest move is to put the scene back in the queue. A scene with
nothing in it is retired rather than retried forever.

Identity is required, exactly as it is for a persona picture in a conversation.
The persona's own missing-conditioning policy then decides whether an
unconditioned fallback is acceptable, so background production never invents a
looser rule than the one the operator already chose.

Recording proposals and gating them first means the ideas can be reviewed, and
the machine protected, before any GPU time is spent.

The API is `/api/v1/scene-backlog`, `/api/v1/scene-backlog/proposals`,
`/api/v1/scene-backlog/{id}/state`, and `/api/v1/scene-backlog/{id}`.

## The retained picture library

A generated picture is kept with the scene that produced it. When a later
request matches a retained picture closely enough, it is served instead of
generated, and the journal records that it was. A picture that already exists
arrives now; a better one that takes forty seconds arrives after the
conversation has moved on.

Matching is over the scene record, never prompt text. Two prompts can describe
the same picture in completely different words, and comparing rendered strings
would either miss that or match things nobody can explain. Scene fields are
comparable because they are separate fields: the subject dominates the score, so
a wrong subject can never be rescued by a matching setting, and a request asking
for detail the stored picture says nothing about scores lower rather than being
ignored.

Two rules keep reuse from reading as a mistake. A picture is never served twice
into the same conversation, and a picture is never recycled back into the
conversation that produced it - asking twice means two pictures, not the same
one returned again.

A picture with no scene is not retained. Without one there is nothing to match a
later request against, and an unmatchable library is only disk use. Pictures can
also be added by hand with a description, which is what makes the library useful
before anything generates into it.

`MEDIA_LIBRARY_ENTRY_LIMIT` caps how many entries stay active. Beyond it the
oldest are retired rather than deleted: the picture is still the owner's, and
removing files to save space is not this layer's decision. The API is
`/api/v1/media-library` and `/api/v1/media-library/{id}`.

## The scene contract

A Task Model returns a typed scene - subject, action, setting, wardrobe,
framing, lighting, camera, mood - rather than finished prompt text. Prompt
syntax belongs to the checkpoint, so writing it is a decision the model has no
basis for and cannot make consistently across dialects. The platform renders the
scene into whichever dialect the selected preset declares.

Empty fields are normal: the model fills in what the request implies and leaves
the rest alone. A scene with nothing in it at all is refused, because there is
no picture to make. The platform derives the short summary shown on the
capability request from the subject, action, and setting; the full scene is what
the compiler reads, and both appear in the journal.

The model still cannot name a provider, model, LoRA, workflow, resource ID, or
generation setting, and the schema rejects any field it was not offered.

A direct image request has no scene: those are the user's own words, and they
are compiled as written.

## Prompt dialect

Prompt syntax is a property of the checkpoint, not of the request. A model
resource carries a `prompt_dialect` in its default settings declaring style
(`natural_language`, `booru`, or `hybrid`), prefix and suffix templates, its own
negative prompt, whether it supports a negative prompt at all, where LoRA
trigger words belong, and an optional target length.

A deterministic compiler renders the request into that dialect before
submission: the same request and dialect always produce the same text, which is
what makes the compiled prompt worth recording. Both the positive and negative
text appear in the generation journal along with the decisions taken, so an
operator can see exactly why the submitted text differs from what was asked for.

The platform safety negative applied when NSFW output is disabled is kept
separate from the model's own negative, so editing one never silently weakens
the other. A dialect declaring no negative support sends none - and the journal
records that the safety negative could not be carried, rather than implying it
was.

A model with no configured dialect uses a default that reproduces the previous
behavior exactly, so nothing changes until an operator edits it. That default is
a starting point, not a recommendation: the quality boilerplate it carries suits
older Stable Diffusion checkpoints and actively harms several current families.
Dialects are edited today through the advanced default-settings editor; a
purpose-built editor arrives with presets.

## Declared request inputs

A workflow must say where the request goes. `prompt_bindings`,
`negative_prompt_bindings`, `seed_bindings`, `width_bindings`, and
`height_bindings` are exact `{node_id, input_name}` pairs validated against the
inline graph, exactly like the image bindings below. An enabled workflow with a
non-empty graph and no prompt binding is refused, because a workflow that cannot
receive the request renders the text saved inside it and still returns a
picture: the failure would be invisible.

Nothing is guessed. Choosing which node was meant to receive the prompt on the
operator's behalf could change what an already-tested graph produces, so the
binding is always an explicit choice.

Workflow import inspection reports the candidates ComfyUI proves are writable:
text inputs for the prompt, integer `seed` or `noise_seed` inputs, `width` and
`height`, and the checkpoint input described below. Each candidate carries the
value currently saved in it, which is how an operator tells a positive prompt
input from a negative one. An input already fed by another node is never
offered, because overwriting it would break the operator's own wiring.

A workflow with declared prompt bindings executes as the whole graph. Its own
sampler and LoRA wiring are used as saved, and only the declared inputs are
replaced.

## Workflow templates

Nice Assistant ships known-good ComfyUI graphs with their bindings already
declared. Setting identity conditioning up used to mean exporting a graph in API
format, reading its nodes, and choosing which input receives the prompt and
which receives the reference; that is a node-graph task, and it is the part a
person should not have to do to get started.

A template carries its graph with fixed node IDs, its bindings, the mechanism it
implements, and the checkpoint families it was built for. Installing one writes a
workflow resource paired with a chosen catalog model, so the graph takes that
model through its checkpoint binding rather than the placeholder name inside the
shipped file.

A template also says what it makes - pictures or video clips - and is offered
only to models of that kind, the same choice the import card puts to a person.
A video template declares no identity mechanism, is refused if it claims one,
and is checked for its nodes, its files and a place for the prompt rather than
for a reference path it could not have.

Inspection changes role. For an imported graph it is discovery - which inputs
could receive what. For a template it is verification: are these node types
installed, and are the files these nodes name present? What it cannot see is
said in plain language rather than implied. An identity model a node picks by
device rather than by a named input does not appear in `/object_info` at all, so
a template lists it under what you need to have installed and the check says so.

When a check reports that a file the graph names is not installed, it also
returns the files ComfyUI does have for that input, and the template offers them
as a choice. A downloaded model keeps whatever name its source gave it - several
arrive as `diffusion_pytorch_model.safetensors` - so the graph is pointed at the
file rather than the file renamed to match the graph. A choice may only name a
file input that already exists in that template's graph; it is a way to say
which file, not a way to edit the graph.

Nothing here claims a template has been run on this deployment, because it has
not been. The first requested persona image is still the live test, exactly as
it is for a workflow an operator imported themselves.

A model resource may declare its `architecture` - `sd15`, `sdxl`, `pony`,
`illustrious`, `sd3`, `flux`, `chroma`, `wan`, or `other`. It is declared, never
sniffed: the application has no access to the models directory, and
`/object_info` reports the filenames a provider has rather than what is inside
them. It matters because an identity adapter is trained against one text
encoder, and families that share a base architecture do not necessarily share
that encoder. A mismatch is shown and marked rather than hidden - the operator
may know something the declaration does not - and an undeclared family offers
everything and asks for the declaration.

A template whose mechanism is `identity_pass` can be installed straight into a
recipe as a later pass. Otherwise the only way to use one would be to hand-edit
a preset's definition JSON, which is the node-graph problem again in a different
costume.

Installing records `source_template_id` and `source_template_version` on the
resource. A newer version of a template is offered, never applied: installing it
writes a second workflow and leaves the first alone, because the graph in the
catalog may have been tuned since. Null provenance is the normal state and means
the graph did not come from a template.

A workflow may declare `consumes_prompt: false`. A pass that only changes a
picture it is handed - a face swap over a finished image - has no text input at
all, and its only string widgets are face indexes; binding the request into one
of those would be worse than having no binding. Such a workflow is exempt from
the prompt-binding rule and refused if it also claims it can generate, because
a graph that takes no prompt cannot make a picture from one.

Some identity techniques only condition when a particular word appears in the
prompt, and produce an ordinary picture without saying anything when it does
not. A workflow may declare `required_prompt_token` together with the
`prompt_prefix` that supplies it; the executor prepends the prefix when the
compiled prompt lacks the word, and refuses to run when a workflow declares a
word it has no prefix for. A refusal is better than an unconditioned picture
presented as the persona.

## The checkpoint a graph loads

A ComfyUI graph carries the checkpoint it was saved with, so a preset could name
one base model and render another - and the picture still came out, which made
the mismatch invisible. Two things now stop that.

`checkpoint_bindings` names a `ckpt_name`, `checkpoint_name`, or `unet_name`
input the preset's base model is written into at run time. These are combo
inputs rather than free text, so inspection offers them separately from the
prompt and never confuses the two. Guided identity setup binds one
automatically when the graph has exactly one such input, because there is then
no ambiguity about what it loads; with a refiner there are two, and guessing
would change what an already-tested graph produces.

Without a checkpoint binding, a preset whose graph bakes a different checkpoint
name is refused at save time, naming both files. The fix is either to bind the
input or to point the preset at the model the graph really loads. A graph that
loads no checkpoint by name - because a parent workflow supplies it - is
unaffected. A workflow saved before bindings existed still runs
through the previous merge-over-a-default-graph path, so it produces exactly
what it produced before; it is reported with `needs_binding_review` until an
operator opens it and chooses its prompt input. That flag is derived from the
resource rather than stored, so it cannot drift, and a plan that selects such a
workflow carries a visible warning.

An enabled ComfyUI `image_to_image` workflow must also declare exact
`source_image_bindings`. Enabled `inpaint` and `outpaint` workflows additionally
require `mask_image_bindings`. Nice Assistant uploads the owner-selected
protected media through `/upload/image` and replaces only those declared inputs;
the graph remains responsible for converting the mask image into the node type
its custom nodes require.

The current semantic vocabulary is controlled by the server. Task Models may
request generation domains, content tags, or features from that vocabulary, and
describe the picture as a typed scene, but cannot name a provider, model, LoRA,
workflow, URL, or generation setting.
Unknown semantic values are rejected. Editing remains explicit-only because the
Task Model does not yet have a typed resolver for protected chat attachments.

Catalog settings include a planning VRAM budget and maximum LoRA count. These
describe estimated job demand, not live GPU measurements. An estimate of zero
means unknown and produces a warning; it does not prove that a resource is free
or loaded. When GPU coordination is enabled, provider-reported free capacity is
compared with the selected plan's estimate and configured reserve. The estimate
does not become telemetry, and external services continue to own model loading
and GPU residency.

## Planning and execution

For a model-requested capability, the coordinator:

1. applies hard kind, operation, content, and feature requirements to every
   enabled preset;
2. scores the survivors by explicit domain coverage, then operator priority,
   then estimated cost;
3. rejects selections that exceed the configured VRAM budget;
4. fills only the preset's declared open slots, still gated by the explicit
   compatibility edges; and
5. persists an immutable, explainable plan naming the chosen preset, its
   revision, and the reason it won, before any provider work begins.

Execution revalidates the preset revision alongside the resource revisions. A
preset edited after planning produces the same retryable failure a changed
resource does; nothing is ever silently substituted.

For an explicit conversational image request, a ready plan is revalidated and
queued automatically. Editing, disabling, or deleting a selected resource makes
the plan stale and produces a compact retryable attachment failure; execution
never silently substitutes a resource. Video retains explicit approval, and its
saved plan is revalidated after approval and before submission. Selected
resources, reasoning, estimates, warnings, and rejection reasons remain
available through collapsed attachment Details and authenticated diagnostics.
A blocked image plan does not become a pending approval card. It becomes a
retryable failed attachment, and retry creates a new request and plan from
current settings. Initial planning always retains the persona that originated
the turn even if the chat switches personas while the Task Model is still
running. Persona-chat planning derives
`identity_control` from the Task Model's typed `persona_subject` decision; the
user's requested subject is authoritative and persona reply prose cannot expand
it. See ADR 0017.

When `identity_control` is required, planning first looks for a persona chat, an
active consented identity profile, an approved primary reference whose file still
matches its reviewed digest, and a compatible bound ComfyUI workflow. A ready
workflow produces the existing conditioned snapshot. When conditioning cannot
be completed and the effective policy is `allow_unconditioned`, the planner may
select an ordinary model while preserving a durable `unconditioned` snapshot
and an explicit resemblance warning. That policy is the default even when no
profile exists; unconditioned execution does not transmit or use identity
references and therefore does not require consent or a reference. `require_conditioning`
remains blocked. Execution revalidates the saved policy and any snapshotted
profile revision immediately before provider submission. Conditioned candidates
still require current consent and reference evidence, may be compared inline,
and keep every generation/comparison/correction attempt durable.

ComfyUI plans execute `generate`, `inpaint`, `outpaint`, and `image_to_image`
only when their exact inputs are configured. Automatic1111 and cloud media
adapters remain generation-only. Explicit edits use
`POST /api/v1/media/image-edit-jobs`; inpaint and outpaint require both an
owner-scoped source media ID and mask media ID.

A comparison is advisory measurement, not the means by which a persona keeps a
consistent face; that is the persona's declared conditioning mechanism. When an
operator has deliberately enabled the retry loop and a real comparison falls
below threshold, the attempt policy reruns up to the snapshotted limit. A compatible identity-control image-to-image
workflow receives the previous candidate through its source binding; otherwise
the original graph reruns. Sequential stages reserve the maximum stage estimate,
including compatible LoRAs, rather than summing stages that never coexist.

Direct media buttons remain explicit manual actions. They receive a durable
`manual` plan explaining that the operator's legacy provider settings were used
and that coordinator selection was bypassed. This preserves the existing UI
without representing it as catalog-planned generation.

## Generation journal

Every generation writes exactly one journal, whatever started it: a
conversational request, a direct button, an edit, or a library-served picture.
The origin is derived from the durable plan rather than supplied by the caller,
so no path can be added later that forgets to record itself.

A journal holds ordered, timed stages: the request, the selected plan with the
coordinator's reasoning, identity conditioning, each attempt, the provider
request and response, storage, any identity comparison, and the outcome. Later
work in the image generation program adds its own stages rather than replacing
the record.

It is reached in one click from the picture in the conversation, not from
settings, because the question it answers is asked while looking at the image.
It exports as one Markdown document that can be handed to another person
alongside that image.

Credentials, provider addresses, and absolute server paths never reach a
journal. Redaction runs before anything is stored, so an export needs no
sanitizing step of its own; file locations appear as names only. A journal is
deleted with the media it describes, and `MEDIA_JOURNAL_RETENTION_DAYS` bounds
how long the rest are kept.

The API is `/api/v1/media/{id}/journal`, `/api/v1/media-journals`,
`/api/v1/media-journals/{id}`, and `/api/v1/media-journals/{id}/export`.

## Migration and privacy

Migration `0010_media_catalog` imports each owner's enabled legacy image/video
configuration as catalog model resources and marks that import complete.
Accounts configured after migration are imported lazily on first catalog use.
Existing settings remain available to the manual generation path during the
transition.
Migration `0014_media_correction_workflows` adds the owner-scoped attempt ledger
without reconstructing existing plans or media.
Migration `0015_media_provider_bootstrap` repairs accounts that enabled a media
provider after the one-shot import had already completed, but only when the
matching catalog kind is empty. Future disabled-to-enabled settings changes use
the same missing-kind rule. Existing operator resources are never overwritten
or recreated; see ADR 0016.
Migration `0024_media_generation_presets` adds the preset table. It is
additive; owners are backfilled lazily on first use rather than rewritten.
Migration `0023_media_generation_journal` adds the journal and its stages.
It is additive: existing plans, attempts, and media are untouched, and anything
generated before it simply has no journal.
Migration `0016_identity_fallback` adds the explicit no-workflow policy to
existing visual-identity profiles. It does not rewrite reviewed references,
media, or completed plans.

Resource metadata and plans are owner-scoped. Prompts remain in their existing
capability request; execution plans store semantic requirements and selected
resource snapshots but do not duplicate the prompt. Content tags describe
technical fitness and never bypass capability permission, provider policy, or
future identity/consent rules.

## Deliberate boundaries

- Persona visual identity persistence and comparison remain a separate trust
  boundary. Step 18B consumes reviewed references without changing what
  `verified` means.
- Live admission for catalog-planned local image generation is delivered in
  Step 18A. Direct manual actions and zero estimates bypass it truthfully.
- Multi-reference fusion, automatic mask creation, and Task Model attachment
  resolution remain future work. Live 12 GB performance tuning belongs to real
  deployment acceptance.
- The legacy modeled-residency layer is deleted; coordination uses only real
  provider telemetry/control and explicit catalog estimates.

The canonical operator surface is Settings -> Media Catalog. The API is
`/api/v1/media-catalog`, `/api/v1/media-catalog/settings`,
`/api/v1/media-catalog/resources/{id}`,
`/api/v1/media-catalog/identity-workflows/inspect`,
`/api/v1/media-catalog/plan-previews`, `/api/v1/media-plans/{id}`, and
`/api/v1/media-plans/{id}/attempts`.
