# Persona visual identity

## Trust boundary

Nice Assistant is the source of truth for persona identity profiles, explicit
consent, reference provenance, review state, validation history, and deletion.
The initial CompreFace adapter uses its documented two-image verification API as
a stateless LAN comparison service. Nice Assistant does not enroll a subject or
store a second reference gallery in CompreFace.

Reference images are biometric-adjacent sensitive artifacts. Uploads are limited,
decoded with Pillow, bounded by pixel count, converted to RGB, resized when
necessary, and re-encoded as metadata-free JPEG files. Original upload bytes and
metadata are not retained. Stored provider credentials are encrypted with
`NICE_ASSISTANT_MASTER_KEY`.

## Durable states

A profile is `draft`, `active`, or `disabled`. Consent is separately
`not_granted`, `granted`, or `withdrawn`. A profile becomes active only when
consent is granted and at least one reference has been explicitly approved.

References are `pending`, `approved`, `rejected`, or `deleted`, with provenance
of `user_upload`, `imported`, or `generated_approved`. New files never become
approved automatically. Deletion removes the file and retains a tombstone and
safe audit event. Withdrawing consent deletes every reference file, disables the
profile, and cancels in-process validation work.

Candidate validations are durable jobs or inline media stages and records. They move through `queued`,
`running`, then `passed`, `failed`, `error`, or `cancelled`. Startup converts
unfinished validation records to a safe `interrupted by server restart` error.
Only `passed` maps to a `verified` identity claim. Below-threshold results map to
`rejected`; provider errors, cancellation, and missing configuration remain
`unverified`.

The comparison record stores the best similarity, threshold, matched reference
ID, face counts, provider/version metadata when supplied, a safe request ID, and
redacted errors. It does not store raw embeddings. Similarity is an operator aid,
not proof of a real person's legal identity.

## Operator flow

Settings -> Visual Identity provides the review workflow:

1. Select a persona and enable private visual-identity storage. For a fictional
   persona this confirms only that the operator created the image or has the
   right to use it; it does not imply that a real person is granting consent.
2. Upload an image or choose one from the owner-protected generated-image
   gallery. Raw database or protected-media IDs are not user-facing inputs.
3. Review and explicitly approve, reject, or delete each pending reference.
4. Record stable appearance guidance and configure an identity-aware ComfyUI
   workflow if new generations should actually use the approved reference.
5. Optionally configure the separate LAN verifier under Advanced settings when
   automated comparison, retry, or blocking is wanted.
6. Optionally choose a generated image from the thumbnail gallery for manual
   comparison and inspect the durable result and audit history.

The readiness card reports reference approval, reference-aware generation,
optional comparison, and automatic blocking independently. CompreFace is only a
stateless verifier: it can compare a result to an approved reference, but it
cannot improve generation or make an image resemble that reference.

The appearance description is snapshotted into identity-aware plans
and added to the generation prompt. The approved primary reference is separately
uploaded into the selected ComfyUI workflow's explicit identity bindings. The
configured retry limit now bounds automatic attempts.

After each conditioned candidate, the configured verifier compares the exact
snapshotted approved reference. Below-threshold candidates trigger bounded
reruns; when an eligible ComfyUI image-to-image identity workflow is configured,
the next stage binds the prior candidate as its source. `block_claim` withholds
every rejected candidate from the capability result, while `show_unverified`
returns the best-scoring candidate with an explicit unverified claim. Provider
unavailability is not evidence of a mismatch, so it does not trigger retries.

## APIs

- `GET /api/v1/media?kind=image` (owner-scoped protected media picker)
- `GET/PUT /api/v1/identity-validation/settings`
- `POST /api/v1/identity-validation/check`
- `GET/PUT /api/v1/personas/{id}/visual-identity`
- `POST/DELETE /api/v1/personas/{id}/visual-identity/consent`
- `POST /api/v1/personas/{id}/visual-identity/references`
- `POST /api/v1/personas/{id}/visual-identity/references/from-media`
- `POST /api/v1/identity-references/{id}/approval`
- `POST /api/v1/identity-references/{id}/rejection`
- `GET /api/v1/identity-references/{id}/content`
- `DELETE /api/v1/identity-references/{id}`
- `POST/GET /api/v1/personas/{id}/visual-identity/validations`
- `GET /api/v1/personas/{id}/visual-identity/history`
- `GET /api/v1/media/{id}/identity-status`
- `GET /api/v1/media-plans/{id}/attempts`

Every lookup is owner scoped. Reference content uses authenticated protected
delivery and is included only in full backups.

## Generation and correction boundary

Identity-aware media may use an active, consented profile and reviewed reference only when the
platform planner requests `identity_control` and the catalog selects a real bound
ComfyUI workflow. It preserves the exact profile revision, reference digest, and
workflow in the media plan. This is conditioning, not verification. Ordinary
generation makes no identity claim, and only an accepted comparison can produce
`verified`. Each attempt and comparison is durable. Rejected intermediate
artifacts remain protected and queryable to their owner through attempt audit,
but are never rendered as the persona result under `block_claim`.
