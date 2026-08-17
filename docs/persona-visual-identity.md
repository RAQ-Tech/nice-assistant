# Persona visual identity

## How resemblance is produced

Resemblance comes from a declared conditioning mechanism recorded on the
persona, not from generating candidates until a comparison passes. A profile
carries which mechanism applies - reference-adapter conditioning, or an identity
pass in a multi-pass preset - alongside its approved reference set and canonical
appearance text. That is the Identity Spec, and it is the control.

A persona also records which presets are known to work for it. Whether a
recipe's conditioning actually holds for one particular face is not something a
score can represent, so it is remembered rather than rediscovered. Routing
consults it after the task model's own choice and never before the hard filter.

Presets declare which mechanisms they implement, and a persona image plans only
against one that can honor the spec. A preset without the right wiring is
rejected by name rather than quietly producing an unconditioned result.

A comparison afterwards is advisory measurement. It scores a finished candidate
and labels it; nothing about scoring a result makes the next result better. The
comparison-driven retry loop from ADR 0013 still exists and is still bounded,
but it is off unless an operator deliberately switches it on, because resampling
until a check passes is a check standing in for a control.

Nothing requires a verifier to be running. Readiness is answered on demand and
never polled on a timer, so an optional comparison service imposes no standing
cost. A persona image generates, is labeled `unverified`, and attempts no
comparison when none is configured - a normal state, not a degraded one. The one
unchanged exception is a profile whose saved policy is `require_conditioning`,
which still blocks, because that policy is about conditioning rather than
measurement.

`verified` still means what it meant. It requires a real passed comparison, per
ADR 0010 and ADR 0012. Turning comparison off does not turn unverified results
into verified ones; it means results are labeled unverified, which is the
truthful description of an unmeasured image. See ADR 0031.

## What comparison is actually good for

Now that resemblance comes from a declared mechanism, the useful question a
verifier answers is not "is this picture allowed" but "how much likeness does
this combination cost". An identity adapter is trained against one text encoder,
so it holds a face well on the family it was built for and less well elsewhere.
Nothing can tell you how much less without measuring it.

That is calibration, and it is worth running deliberately and then turning off:
generate the same scene against two checkpoint families, compare each result
with the approved reference, and read the scores side by side. What comes back
is a number for a question that would otherwise be answered by squinting. It is
also how a threshold gets chosen honestly - a number picked before any
measurement is a guess wearing a decimal point.

Leaving a verifier switched on as a gate is a different and worse proposition. A
comparison service is a separate deployment that has to be running, it adds a
round trip to every persona picture, and blocking on it converts a measurement
into a refusal for a picture that may be perfectly good. `show_unverified` is
the default for that reason: the honest label costs nothing, and the block costs
a picture.

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

Settings -> Persona Pictures is one surface for a persona's appearance: reviewed
references, the Identity Spec, and the pictures kept for reuse. It replaced the
separate Visual Identity tab rather than adding to it.


The review workflow is:

1. Select a persona and enable private visual-identity storage. For a fictional
   persona this confirms only that the operator created the image or has the
   right to use it; it does not imply that a real person is granting consent.
2. Upload an image or choose one from the owner-protected generated-image
   gallery. Raw database or protected-media IDs are not user-facing inputs.
3. Review and explicitly approve, reject, or delete each pending reference.
4. Record stable appearance guidance and choose what happens while a
   reference-aware workflow is unavailable: generate with a visible warning or
   require conditioning and block.
5. Use the focused Identity Control setup in Media Catalog to import a ComfyUI
   API-format workflow, inspect installed nodes/assets, and bind the reviewed
   reference input when new generations should use the approved reference.
6. Optionally configure the separate LAN verifier under Advanced settings when
   automated comparison or retry is wanted, and choose the visible policy used
   after a measured comparison failure.
7. Optionally choose a generated image from the thumbnail gallery for manual
   comparison and inspect the durable result and audit history.

The readiness card reports reference approval, reference-aware generation,
the saved no-workflow behavior, optional comparison, and the saved
comparison-failure behavior independently. CompreFace is only a stateless
verifier: it can compare a result to an approved reference, but it cannot improve
generation or make an image resemble that reference.

`allow_unconditioned` keeps image generation available when no compatible
identity workflow is configured. This is the effective default even before a
visual-identity profile, consent grant, or reference exists. The durable plan
and result are labeled `unconditioned` and `unverified`. Compact attachment
Details state that no persona identity reference was applied and resemblance is
not guaranteed. The explicit image request runs without a second approval.
Unconditioned execution never transmits or uses a reference;
saved appearance guidance is included only from an active, consented profile.
`require_conditioning` prevents execution and produces a compact retryable
failure until setup is complete.

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
- `POST /api/v1/capability-requests/{id}/replan`
- `POST /api/v1/media-catalog/identity-workflows/inspect`
- `GET /api/v1/media-catalog/workflow-templates`
- `POST /api/v1/media-catalog/workflow-templates/{id}/verify`
- `POST /api/v1/media-catalog/workflow-templates/{id}/installations`

Every lookup is owner scoped. Reference content uses authenticated protected
delivery and is included only in full backups.

`PUT /api/v1/personas/{id}/visual-identity` writes the whole profile. A field the
caller omits takes its documented default rather than keeping its stored value,
so a client must send back the profile it read. The body may carry the `revision`
it read; when it does, a write from a stale copy is refused with `409` instead of
overwriting fields it never saw. Two surfaces write this profile - the identity
behavior controls and the picture library's preferred recipes - so that refusal
is the difference between a lost preference and a visible one. Omitting
`revision` keeps last-writer-wins for any client that predates the guard.

A persona may have several approved reference photos, and they are used
together. A workflow declares how many it can take by how many image inputs it
binds - PhotoMaker stacks up to three into a steadier likeness than any one shot
gives, InstantID takes one - and the photos cycle over those slots, so fewer
photos than slots repeats rather than leaving a slot pointing at a file the
provider does not have. The plan pins every photo it used by checksum, all of
them are re-checked before execution, and a set where one member changed is
treated as a different set.

A persona declares how its face is produced. `reference_adapter` conditions
generation on the approved reference; `identity_pass` generates the picture and
then replaces the face, which is the only option for checkpoint families no
adapter was trained against, and which cannot change pose or lighting. The
settings control offers exactly the mechanisms this catalog can apply, reported
with the profile, plus whatever the persona is already set to - a choice that
can only block is worse than no choice. Every picture records which technique
produced its face.

Setting identity conditioning up starts from a shipped workflow template rather
than from an exported graph. The templates carry their own bindings, so nothing
asks which node receives the prompt or the reference; checking one asks ComfyUI
whether its node types and named files are installed, and states plainly what it
could not check. Importing a graph of your own remains available behind a
disclosure, because a graph somebody has already tuned is worth more than a
shipped one. See `docs/media-catalog.md`.

`POST /api/v1/media-catalog/identity-workflows/inspect` returns both
`identity_input_candidates`, which name where an approved reference can be bound,
and `request_input_candidates`, which name the literal prompt, seed, width, and
height inputs the platform could write a request into. Guided setup needs both:
an identity workflow that cannot receive the request prompt renders whatever text
was saved inside it.

## Generation and correction boundary

Reference-conditioned media may use an active, consented profile and reviewed
reference only when the platform planner requests `identity_control` and the
catalog selects a real bound ComfyUI workflow. It preserves the exact profile
revision, reference digest, and workflow in the media plan. This is conditioning,
not verification. When the saved policy permits an ordinary fallback, appearance
guidance may still be used but the reference is not sent and the result makes no
identity claim. Only an accepted comparison can produce `verified`. Each attempt
and comparison is durable. Rejected intermediate artifacts remain protected and
queryable to their owner through attempt audit, but are never rendered as the
persona result under `block_claim`.
