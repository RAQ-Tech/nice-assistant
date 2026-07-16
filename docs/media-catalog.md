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

## Planning and approval

For a model-requested capability, the coordinator:

1. applies hard kind, operation, content, and feature requirements;
2. scores the remaining enabled base models by explicit domain coverage and
   operator priority;
3. rejects selections that exceed the configured VRAM budget;
4. selects only explicitly compatible LoRAs and, when relevant, a compatible
   ComfyUI workflow; and
5. persists an immutable, explainable plan with resource revisions before the
   browser presents the approval card.

The approval card shows the selected resources, reasoning, estimates, warnings,
and blocked state. Approval revalidates every selected resource. Editing,
disabling, or deleting a selected resource makes the old plan stale and prevents
execution; the system does not silently re-plan after the user has reviewed it.
Blocked cards also show the hard requirements and per-resource rejection reasons.
Their active remediation action carries the request/persona context into the
Identity Control setup and can explicitly recheck a still-pending blocked plan.
Rechecking retains the plan's originating persona and rejects a changed chat
persona. Legacy blocked plans without the newer persona snapshot adopt the
current chat persona once and record that recovery in the replan audit.
Persona-chat planning derives
`identity_control` from the Task Model's typed `persona_subject` decision; the
user's requested subject is authoritative and persona reply prose cannot expand
it. See ADR 0017.

When `identity_control` is required, planning first looks for a persona chat, an
active consented identity profile, an approved primary reference whose file still
matches its reviewed digest, and a compatible bound ComfyUI workflow. A ready
workflow produces the existing conditioned snapshot. When only the workflow is
unavailable and the saved profile policy is `allow_unconditioned`, the planner
may select an ordinary model while preserving a durable `unconditioned` snapshot
and an explicit resemblance warning. `require_conditioning`, missing consent,
or changed reference evidence remains blocked. Approval revalidates the saved
policy and profile revision. Conditioned candidates may be compared inline, and
each generation/comparison/correction attempt remains durable.

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
Migration `0016_identity_fallback` adds the explicit no-workflow policy to
existing visual-identity profiles. It does not rewrite reviewed references,
media, or completed plans.

Resource metadata and plans are owner-scoped. Prompts remain in their existing
capability request; execution plans store semantic requirements and selected
resource snapshots but do not duplicate the prompt. Content tags describe
technical fitness and never bypass capability approval, provider policy, or
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
