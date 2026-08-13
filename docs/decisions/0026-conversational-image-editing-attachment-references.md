# ADR 0026: Conversational image editing through attachment references

- Status: accepted
- Date: 2026-08-13
- Owners: Nice Assistant maintainers

## Context

Explicit image editing has existed since Step 18C, but only through the direct
API path where the owner selects a source image themselves. The Task Model was
excluded from `media.edit_image` entirely, and the planning vocabulary was
hardcoded to `generate`, because the model had no safe way to name an existing
image. Handing it a media identifier would have made resource identity a model
output, which every prior media decision deliberately avoids: a hallucinated or
copied identifier would reach another artifact, and the task model has no
authorization context to bound it.

The deterministic action gate compounded this. It classifies most edit phrasing
as non-actionable, which was correct while editing could not be planned at all,
but it means an edit request would be discarded even once a safe typed input
exists.

## Decision

- The platform publishes the current chat's completed image attachments to the
  planner as opaque references (`conversation_image_1`, oldest-to-newest counted
  back from the most recent) with a short description. Media identifiers are
  never sent to the model.
- `source_attachment` and `mask_attachment` are optional enum-bounded fields on
  a planned capability request. Their enums contain only labels the platform
  published in that same request, plus an explicit empty sentinel. A generation
  request keeps its previous object shape exactly.
- References are resolved back to artifacts by the platform when the planned
  request is prepared. Resolution uses the exact bindings that were offered to
  the planner, carried through the follow-up result and never persisted, so a
  picture that completes between planning and preparation cannot silently take
  over a label. The resolved artifact is then re-checked against the chat's
  current editable attachments, which keeps the ownership and chat-scope
  boundary live rather than trusting the snapshot. A reference that fails either
  check drops the request rather than editing a different image or degrading
  into a generation.
- Editing is offered only when the catalog has a ready editing operation *and*
  this conversation holds at least one resolvable image. The available
  operations vocabulary is derived from the ready operations rather than
  asserted.
- A planned edit is created `pending_confirmation`. Automatic execution is
  driven by the capability definition's permission mode rather than by media
  kind, so image generation keeps its ADR 0019/0023 auto-run behavior while
  editing does not inherit it.
- Editing uses a separate deterministic gate,
  `is_high_confidence_image_edit_request`. It requires both an explicit change
  verb and a reference to an image that already exists, and it refuses quoted,
  hypothetical, explanatory, text-about-an-image, and library-management
  phrasing. The creation gate is unchanged.

## Alternatives considered

- Let the task model supply a media ID directly. Rejected: it makes resource
  identity a model output and removes the authorization boundary.
- Reuse the creation gate for edits. Rejected: it rejects ordinary edit phrasing,
  so the typed input would exist but never be reachable, which would advertise a
  capability that does not work.
- Widen the creation gate to accept edit phrasing. Rejected: that weakens the
  story/discussion guard protecting automatic generation. A separate gate keeps
  the auto-run boundary exactly where it was.
- Auto-run planned edits like generation. Rejected: an edit consumes an existing
  artifact the user may care about, and ADR 0019 scoped auto-run to ordinary
  image *requests*. Confirmation is retained until installed acceptance shows it
  is safe to relax.
- Offer every image the owner has. Rejected: chat scope is what makes "that
  picture" unambiguous, and a wider list would let a planned edit reach an
  artifact outside the conversation.

## Consequences

`MediaTaskRequirements` carries resolved `source_media_id` and `mask_media_id`,
which appear in stored arguments only when set, so existing generation records
and their idempotency comparisons are unchanged. Planned edits build an edit plan
rather than a coordinator plan, and therefore require a ComfyUI workflow with
real source bindings, plus mask bindings for inpaint and outpaint. Because
editing is confirmation-gated, an accepted edit is always a deliberate user
action even when the planner proposes it.

The edit gate remains biased toward false negatives. Expanding accepted phrasing
is an evaluation task, not a regex change, exactly as for the creation gate.

## Verification

- Contract tests prove the reference enums contain only offered labels, that the
  fields stay optional, that an unavailable or invented reference is refused, and
  that masks are rejected for unmasked operations and for non-edit operations.
- An API test proves a planned edit resolves to the artifact the platform
  offered, builds a ready edit plan, and stops at `pending_confirmation`; and
  that a reference the platform never offered creates no request at all.
- The same test proves label stability: with a newer image present, a plan made
  against the earlier snapshot still edits the image it was offered. Removing
  the snapshot makes that assertion fail, so the guarantee is covered rather
  than incidental.
- Curated gate tests prove the edit predicate accepts explicit changes and
  refuses creation, discussion, captions, quotes, and library management, and
  that the creation and edit gates stay independent.
