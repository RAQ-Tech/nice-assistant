# ADR 0016: Media provider catalog bootstrap

- Status: Accepted
- Date: 2026-07-15
- Owners: Nice Assistant maintainers

## Context

ADR 0009 imported enabled image and video settings into the operator catalog at
migration time or first catalog access. The import-complete flag was global and
one-shot. If a new account opened the catalog while media was disabled and
enabled ComfyUI later, direct image actions worked but conversational capability
planning had no catalog model and remained unavailable. Persisted
`local/comfyui` and `local/automatic1111` aliases also no longer matched the
typed browser's canonical `local` provider value.

## Decision

- Normalize the supported local-provider aliases at both API and browser
  settings boundaries while preserving the selected backend.
- When an owner changes a provider from disabled to enabled, seed the matching
  legacy-compatible catalog model only if that catalog kind has no resources.
- Never update, replace, or recreate an existing operator-managed resource.
- Add a forward migration that repairs already-enabled accounts only when the
  corresponding image or video catalog kind is empty.
- Keep direct actions explicit and catalog-bypassing; conversational requests
  still require typed Task Model planning, a reviewable plan, and approval.

## Alternatives considered

- Re-run lazy import whenever the catalog is empty. Rejected because deleting
  the last resource is an operator action and must not be silently undone.
- Make provider settings continuously overwrite catalog defaults. Rejected
  because the catalog is the operator-owned source of truth for planned work.
- Restore keyword or hidden-tag image routing. Rejected because it bypasses the
  durable permission and planning boundary established by ADRs 0007-0009.

## Consequences

Enabling a provider after onboarding now makes a safe starter resource
available to capability planning. Advanced operators retain full control:
existing catalog content is untouched and later edits are not synchronized from
the direct-action settings. Deployment startup applies the repair migration
before serving requests.

## Verification

- Browser tests cover both legacy local-provider aliases.
- API/service tests prove a late ComfyUI enablement changes saved settings,
  creates exactly one catalog model, and makes image planning available.
- Migration tests prove an affected `0014` database gains the missing resource.
- Full process/container verification and an opt-in live ComfyUI generation
  remain required for release acceptance.
