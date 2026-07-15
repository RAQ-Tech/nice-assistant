# ADR 0013: Measured media editing and identity correction

- Status: accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Catalog metadata could describe editing, but the runtime previously executed
generation only. Identity conditioning also stopped before comparison, so a poor
candidate could be presented as the persona. Provider filenames and model names
cannot safely substitute for real workflow inputs or measured validation.

## Decision

ComfyUI is the first editing adapter. Enabled image-to-image workflows must
declare exact source-image bindings; inpaint and outpaint additionally require
exact mask-image bindings. Explicit `/api/v1/media/image-edit-jobs` requests
carry owner-scoped protected media IDs. The task model continues to advertise
generation only until chat attachments have a typed protected-media resolver.

Identity-conditioned generation invokes the configured stateless verifier after
each candidate. Attempts and comparisons are durable. A real below-threshold
score may trigger another attempt up to the snapshotted profile limit. If a
compatible identity-control image-to-image graph exists, it receives the prior
candidate; otherwise the original graph is rerun. Provider unavailability stays
unverified and does not trigger a retry. `block_claim` withholds rejected output;
`show_unverified` returns the best-scoring candidate with an explicit label.

Sequential stages are admitted using the maximum planned stage estimate because
they do not coexist. Provider-reported capacity still gates the whole job and no
claim is made about controlling verifier or external-client resource use.

## Alternatives considered

- Let the persona or Task Model supply workflow IDs: rejected because resource
  choice and protected media authorization are platform responsibilities.
- Retry on verifier errors: rejected because an outage is not evidence of an
  identity mismatch and could multiply load.
- Hide intermediate records: rejected because operators need provenance to tune
  thresholds and workflows.

## Consequences

ComfyUI retains uploaded reference/source/mask inputs according to its own
policy. Automatic1111 remains generation-only. Automatic mask creation,
multi-reference fusion, and measured tuning on the shared 12 GB GPU remain later
deployment work.

## Verification

Migration tests preserve existing plans/media and enforce attempt constraints.
API/provider tests cover exact bindings, owner isolation, source/mask upload,
failed-then-passed correction, restart recovery, and truthful browser labels.
Live ComfyUI graph compatibility and 12 GB latency/capacity are deployment
acceptance checks, not deterministic CI claims.
