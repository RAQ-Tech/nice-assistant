# ADR 0012: Reviewed-reference persona identity conditioning

- Status: Accepted; setup flow superseded by ADR 0018 and image approval path
  superseded by ADR 0023
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Step 17 established consented persona profiles, reviewed reference images, and
stateless post-generation comparison. The media coordinator could select a
workflow labeled `identity_control`, but that label did not make the reference
an input to generation. Treating ordinary text prompting or a later comparison
as continuity would be misleading.

ComfyUI supports API-format workflow graphs and a real `/upload/image` input
route, while custom identity systems such as IPAdapter, InstantID, and PuLID use
extension-specific node schemas. Nice Assistant should compose a reviewed input
without claiming knowledge of every custom node or making a particular extension
part of its core contract.

## Decision

- `identity_control` is a platform semantic requirement. The Task Model may
  request it when an image is presented as the selected persona, but it still
  cannot select providers, checkpoints, workflows, LoRAs, or references.
- Only a selected ComfyUI workflow with non-empty `identity_image_bindings` may
  satisfy the requirement. Each binding names an exact node and input already
  present in the operator-supplied API-format workflow patch. Automatic1111 and
  ordinary text-to-image plans do not claim reference conditioning.
- Planning binds the chat persona, active consented profile revision, primary
  approved reference ID and digest, appearance-description snapshot, selected
  workflow revision, and bindings into the durable media plan. Missing consent,
  persona, reference, file, or executable binding prevents execution with a
  specific safe reason.
- Pre-submission validation rechecks the profile revision, review state,
  reference digest, file content, and selected resources. Nice Assistant never
  silently switches references or resources after persisting the plan.
- Execution adds the appearance description to the provider prompt, uploads the
  reviewed normalized JPEG to ComfyUI, injects the returned input name only into
  the declared bindings, and then submits the workflow. Cancellation is checked
  before and after each network stage and during history polling. An in-flight
  provider request may remain bounded by its timeout, and a successfully uploaded
  ComfyUI input is subject to that service's retention policy.
- Generated media links back to the immutable plan. API and browser state call
  the result `conditioned` and `unverified`; only the separate Step 17 comparison
  can produce a `verified` claim.

## Alternatives considered

- Infer custom-node inputs from class names. Rejected because extension schemas
  change and inference would make readiness claims that were never validated.
- Send only the appearance description. Rejected because text prompting is not
  a reference-image identity workflow.
- Enroll persona subjects inside ComfyUI or CompreFace. Rejected because it would
  create duplicate identity stores and blur consent/deletion ownership.
- Automatically validate, retry, or inpaint every result. Deferred to Step 18C;
  those are separate measured stages with distinct failure and cancellation
  semantics.
- Add an end-user workflow builder. Rejected for this step. Identity graph setup
  is an operator/developer responsibility in the existing Media Catalog surface.
  ADR 0018 later supersedes this alternative with a guided import, inspection,
  and explicit-binding flow while retaining the exact binding contract.

## Consequences

Operators can use any compatible ComfyUI identity extension by supplying a real
API-format graph and explicit file-input bindings. Nice Assistant remains
extension-neutral and preserves the exact reviewed inputs used. A catalog plan
preview without an actual persona is intentionally blocked for identity
execution while still showing the selected resources.

The first implementation chooses one deterministic primary approved reference.
Multi-reference fusion, automatic comparison, retries, image-to-image,
inpainting, and correction remain Step 18C. Provider-side upload cleanup is not
claimed because the documented local ComfyUI API does not expose a matching
delete-input contract.

## Verification

- Migration `0013_identity_generation` preserves old plans/media and adds the
  generated-media plan link.
- Catalog tests cover binding validation, persona/profile/reference blocking,
  immutable snapshots, stale pre-submission validation, prompt composition, protected provenance,
  and unverified claim state.
- Adapter tests cover multipart upload, exact workflow injection, digest checks,
  and cancellation before upload.
- Browser tests ensure compact attachment state distinguishes conditioning from
  verification without restoring per-image approval.
- Complete verification, process smoke, image build, and installed-container
  smoke are required before publication.
