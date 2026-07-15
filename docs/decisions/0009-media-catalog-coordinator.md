# ADR 0009: Metadata-driven media catalog and deterministic coordinator

- Status: Accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Different image/video models, LoRAs, and workflows are fit for different
operations, subjects, and content. Provider defaults and filenames are not a
reliable basis for choosing them. Giving that authority to a persona or Task
Model would make privileged execution dependent on generated text, while a
single global provider setting cannot express compatibility or explain why a
resource was selected. The 12 GB shared-VRAM deployment also needs an explicit
operator budget without pretending Nice Assistant controls provider residency.

## Decision

- Add an owner-scoped catalog of typed model, LoRA, and workflow resources with
  exact external IDs, controlled semantic metadata, operator priority, estimated
  VRAM/load cost, validated defaults, revision, and explicit compatibility edges.
  ComfyUI workflows use a stable catalog ID plus a required inline workflow patch
  because the current adapter has no named-workflow loading contract.
- Keep the capability Task Model semantic-only. It may describe operation,
  domain, content, and feature requirements from server-provided vocabularies;
  it cannot name or see catalog resources.
- Use deterministic hard filtering and metadata scoring to produce an
  explainable, immutable execution plan before confirmation. Revalidate selected
  resource IDs and revisions at approval instead of silently re-planning.
- Treat configured VRAM as an operator estimate, not provider health, residency,
  or capacity telemetry. Unknown estimates are disclosed.
- Execute only operations implemented by typed adapters. Initially this is
  generation; inpaint/outpaint/image-to-image requests are truthfully blocked.
- Keep direct media actions as an audited `manual` plan that explicitly bypasses
  catalog selection until their browser workflow is migrated.
- Import working legacy image/video settings through migration `0010` so existing
  deployments retain behavior.

## Alternatives considered

- Let the persona or Task Model choose model names and LoRAs. Rejected because
  model output is untrusted and cannot enforce compatibility or operator policy.
- Infer strengths from checkpoint/LoRA filenames. Rejected because names are
  inconsistent and silently turn guesses into execution policy.
- Implement a learned coordinator first. Rejected because the catalog is small,
  operator-curated, and deterministic selection is easier to audit and test.
- Claim live VRAM/residency from configured estimates. Rejected because external
  services own hardware state and no verified control API is used here.
- Couple persona identity to this step. Rejected because identity assets,
  provenance, consent, and validation need their own durable foundation.

## Consequences

Operators must describe resource fitness and compatibility explicitly. Plans are
repeatable, reviewable, owner-scoped, and resistant to prompt attempts to select
privileged resources. Catalog changes intentionally invalidate already-reviewed
plans. Legacy direct actions remain functional but clearly identify their manual
bypass. Actual capacity telemetry, multi-stage workflows, and identity continuity
remain separate later work.

## Verification

- Migration tests prove legacy chats, turns, jobs, media, Task Model profiles,
  and imported resource settings survive while database checks are enforced.
- Service/API tests cover CRUD, owner isolation, compatibility, deterministic
  selection, misleading filenames, VRAM limits, blocked operations, stale-plan
  rejection, manual plans, and execution-option forwarding.
- Adapter tests inspect real Automatic1111 and ComfyUI payloads for selected
  LoRA controls.
- Vitest and Playwright cover operator catalog editing, plan preview, visible
  approval explanations, and blocked approvals.
- Repository verification, process smoke, and installed-container smoke remain
  required before release.
