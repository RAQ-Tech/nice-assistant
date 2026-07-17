# ADR 0019: Low-risk companion capability permissions

- Status: accepted
- Date: 2026-07-16
- Owners: Nice Assistant maintainers
- Supersedes: the mandatory-confirmation rule in ADR 0007 for ordinary image generation
- Superseded in part by: ADR 0023, which removes `always_ask` for images

## Context

Nice Assistant is intended to feel like messaging a person. Asking that person
for an ordinary image and then approving the same request again adds ceremony
without adding meaningful consent. It also exposes provider-plan machinery in
the conversation and makes a standard supporting capability feel broken.

The platform still needs durable intent, deterministic resource selection,
owner scoping, safe errors, cancellation, and audit history. Video, destructive
actions, external side effects, strict identity workflows, and future
consequential capabilities have different risk and permission boundaries.

## Decision

- An explicit user request to generate an ordinary image is permission to queue
  it under the saved `auto_explicit_request` policy. The typed Task Model may
  identify the action, but deterministic platform policy admits and audits it.
- Image generation has no redundant per-image approval path. ADR 0023 replaces
  the former `always_ask` option with a per-persona conversational image
  permission.
- Auto-run is permitted only for high-confidence, explicit action intent.
  Stories, discussion, explanations, hypotheticals, and quoted instructions do
  not create a job.
- Video and destructive or externally consequential capabilities remain
  confirmation-gated. Strict identity conditioning may also require action
  before execution.
- A normal image needs only a ready basic provider. Missing optional identity
  setup falls back to an explicitly unconditioned result by default. New
  identity profiles show a failed comparison as unverified; existing saved
  identity policies are preserved.
- Plans, provider execution, permission decisions, retries, and results remain
  durable and inspectable. Default chat presentation is a compact attachment;
  technical plans live behind authenticated Details or diagnostics.

## Alternatives considered

- Require confirmation for every generated image. Rejected because it repeats
  the user's explicit instruction and harms the core companion experience.
- Auto-run every Task Model media prediction. Rejected because probabilistic
  intent can misread stories or discussion as instructions.
- Remove durable capability records for low-risk images. Rejected because
  reload recovery, cancellation, audit, retries, and provider truth depend on
  the durable lifecycle.

## Consequences

Permission mode gains an audited `auto` state, while `confirm` and `explicit`
remain available. Users upgrading keep their saved identity comparison policy;
the production operator can deliberately change it to `show_unverified` during
milestone promotion. Intent evaluation becomes a release gate before auto-run
is enabled.

## Verification

- Contract tests prove explicit image actions auto-run and cannot be returned
  to per-image approval by an older saved setting.
- Negative intent fixtures prove stories, explanations, hypotheticals, and
  quoted instructions create no image request.
- Installed-browser acceptance proves no-profile generation, compact durable
  attachments, reload recovery, cancellation, retry, and truthful identity
  labels.
- Video and consequential capability tests continue to require confirmation.
