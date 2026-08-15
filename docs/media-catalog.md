# Media model catalog and coordinator

The media catalog is the operator-owned source of truth for image and video
resource fitness. It describes exact provider resources; it never infers
capability from a checkpoint, LoRA, or workflow filename.

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
text inputs for the prompt, integer `seed` or `noise_seed` inputs, and `width`
and `height`. Each candidate carries the value currently saved in it, which is
how an operator tells a positive prompt input from a negative one. An input
already fed by another node is never offered, because overwriting it would break
the operator's own wiring.

A workflow with declared prompt bindings executes as the whole graph. Its own
sampler, checkpoint, and LoRA wiring are used as saved, and only the declared
request inputs are replaced. A workflow saved before bindings existed still runs
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
request generation domains, content tags, or features from that vocabulary, but
cannot name a provider, model, LoRA, workflow, URL, or generation setting.
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

1. applies hard kind, operation, content, and feature requirements;
2. scores the remaining enabled base models by explicit domain coverage and
   operator priority;
3. rejects selections that exceed the configured VRAM budget;
4. selects only explicitly compatible LoRAs and, when relevant, a compatible
   ComfyUI workflow; and
5. persists an immutable, explainable plan with resource revisions before any
   provider work begins.

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

When a real identity comparison falls below threshold, the attempt policy reruns
up to the snapshotted limit. A compatible identity-control image-to-image
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
Migration `0022_media_generation_journal` adds the journal and its stages.
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
