# ADR 0018: Explicit persona-identity fallback and guided setup

- Status: Accepted
- Date: 2026-07-15
- Owners: Nice Assistant maintainers

## Context

ADR 0012 correctly separated reference-conditioned generation from optional
post-generation comparison, but the product later exposed the two as one vague
"Automatic blocking" status. A persona image was blocked before generation when
no `identity_control` workflow existed, while Visual Identity could simultaneously
report that automatic blocking was off. The blocked approval action was disabled,
and its Media Catalog link discarded the persona and request context. The only
configuration path was expert-only raw catalog metadata hidden under Advanced.

That behavior was truthful about the absence of conditioning, but it was neither
operable nor consistent with the user's selected output policy. It also made a
supporting capability prevent otherwise working image generation.

## Decision

- Treat three concerns as separate product states and controls:
  reference-conditioned generation availability, optional post-generation
  comparison availability, and the policy used when either stage is unavailable
  or fails.
- Add a persona visual-identity `conditioning_fallback` policy with two values:
  `allow_unconditioned` and `require_conditioning`. Existing and new profiles
  default to `allow_unconditioned` so image generation continues to work while
  an identity workflow is not configured.
- A persona-subject request always prefers a compatible, explicitly bound
  `identity_control` workflow. When configuration is unavailable and the saved
  policy allows fallback, the coordinator may select an ordinary image plan. The
  implicit policy is also `allow_unconditioned` when no profile exists. The
  durable plan and result are labeled `unconditioned` and `unverified`, preserve
  the persona and any saved profile revision, and warn that no persona identity
  reference was applied and resemblance is not guaranteed. Appearance guidance
  is included only from an active, consented profile.
- Unconditioned execution does not transmit or use a reference, so it does not
  require consent or approved reference state. A saved strict policy, a
  changed snapshotted profile revision, or a stale selected media resource still
  blocks approval. Conditioned plans continue to require current consent,
  reviewed-reference digest, binding, and profile state.
- `failure_policy` continues to govern a real comparison failure only. It does
  not describe or control missing generation conditioning. The browser exposes
  both policies directly and describes inactive saved comparison behavior when
  no verifier is configured.
- A blocked, still-pending capability may be explicitly replanned against current
  settings while retaining its originating persona. A changed chat persona
  requires a new request. Legacy blocked plans created before persona snapshotting
  may adopt the current chat persona once; that adoption is persisted and audited.
  Replanning is user-visible and audited. Ready, approved, running, or completed
  plans remain immutable after review.
- Media Catalog provides a focused Identity Control setup flow. It imports
  ComfyUI API-format workflow JSON, checks the configured provider's node schema,
  reports missing node types or selected assets, discovers candidate image
  inputs, and creates an explicitly bound catalog workflow only when required
  inputs, typed links, an acyclic output path, and a reference-to-identity path
  can be proven from provider metadata. Unprovable custom semantics remain a
  draft. Structural/provider compatibility is not described as a successful live
  generation test or identity-match result.
- A blocked card carries its request/persona context into that setup flow and
  offers an actionable plan recheck instead of a disabled button.

## Alternatives considered

- Keep hard blocking until an operator hand-edits raw catalog JSON. Rejected
  because it leaves a formerly working supporting capability unusable and gives
  the user no practical remediation path.
- Reuse `failure_policy` for missing conditioning. Rejected because a missing
  generation input and a measured post-generation mismatch have different
  evidence, timing, and user consequences.
- Silently remove `identity_control`. Rejected because an ordinary image must
  never be presented as reference-conditioned or identity-verified.
- Claim a workflow is live-tested after `/object_info` inspection. Rejected
  because provider schema compatibility does not prove that the graph, assets,
  prompts, and uploaded reference execute successfully together.

## Consequences

Persona images work by default even on deployments without an identity extension,
profile, consent grant, or reference, but the approval card and final media visibly
disclose that no persona identity reference will be or was applied. Operators who require strict likeness can save
`require_conditioning` and receive a targeted block until setup is complete.

The catalog remains extension-neutral and hardware-agnostic. Nice Assistant can
inspect and bind an already installed ComfyUI graph, but it does not claim to
install custom nodes or model assets or control provider residency.

This decision supersedes ADR 0012's rejection of an end-user setup flow and ADR
0017's unconditional no-fallback rule. Their consent, provenance, exact binding,
subject-authority, and reviewed-plan constraints remain in force.

## Verification

- Migration tests prove existing profiles acquire the explicit fallback policy.
- Service/API tests cover conditioned preference, disclosed unconditioned
  fallback with no profile and with a draft/unconsented profile, strict blocking,
  stale/tampered conditioned-reference rejection, and audited
  replanning of only blocked pending requests, including immutable persona context
  and one-time recovery of legacy plans without a persona snapshot.
- Provider-contract tests use deterministic ComfyUI `/object_info` fakes for
  detected inputs, required-input and graph-path validation, missing nodes/assets,
  authentication, bounded responses, and safe errors.
- Browser tests keep both policies editable and visible, preserve targeted setup
  context, import and bind workflow JSON, and make blocked-card remediation work.
- Installed-container and live deployment acceptance exercise the exact chat
  approval and reveal path used by the operator.
