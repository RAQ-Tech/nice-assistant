# ADR 0010: Stateless persona identity verification

- Status: accepted
- Date: 2026-07-14
- Owners: Nice Assistant maintainers

## Context

Persona immersion breaks when generated media is presented as the persona but
does not resemble them. Prompt descriptions are not identity persistence, while
storing unmanaged face embeddings or enrolling a second provider-side gallery
would widen the biometric and deletion boundary. The application container must
remain hardware-agnostic and the current 12 GB shared-VRAM deployment should not
gain an embedded face model.

CompreFace documents a self-hosted REST service and a two-image face verification
operation. That operation permits comparison without creating provider-side
subjects or retaining a second gallery.

## Decision

Nice Assistant stores consent-bound, owner-scoped reference assets and durable
review/validation records. It sends one approved reference and one candidate to
a separate LAN `IdentityVerificationProvider` for stateless comparison. The
initial adapter targets CompreFace's documented verification endpoint.

References are decoded and re-encoded without metadata. API credentials are
encrypted. No raw embeddings are persisted. Generated media is `verified` only
after a real comparison meets the profile threshold; failures are `rejected`,
and missing/unavailable validation remains `unverified`. A task or persona model
cannot override that state.

## Alternatives considered

- Enroll each persona as a CompreFace subject. Rejected because it duplicates
  reference storage and makes complete consent withdrawal depend on two stores.
- Embed a face model in Nice Assistant. Rejected because it couples the lean app
  container to GPU/CPU inference, model lifecycle, and hardware assumptions.
- Persist face embeddings in SQLite. Rejected for the initial implementation
  because raw comparison vectors increase sensitivity and provider lock-in.
- Ask a multimodal Task Model whether images look alike. Rejected because its
  output is not a stable, bounded identity-verification contract.
- Treat prompt/LoRA selection as proof of identity. Rejected because generation
  inputs cannot establish that the output preserved the persona.

## Consequences

The verifier can be replaced behind a narrow contract, and its outage does not
make false claims. Full backups now include sensitive identity references when
media is requested. CompreFace compatibility requires an operator-managed LAN
service and API key. Cancellation closes an established HTTP response, but a
blocking connection attempt may run until its configured timeout.

Step 18 must consume the durable identity profile and result contract rather than
creating a parallel identity system. Provider URL and network policy receive
additional global SSRF hardening in Step 19.

## Verification

- Migrate a populated Step 16 database without changing persona, media, or job
  rows and enforce identity state constraints.
- Prove consent gates uploads, references require review, files are normalized,
  and withdrawal deletes stored files.
- Prove API keys are encrypted and reference/content/validation lookups are
  owner scoped.
- Exercise passed, below-threshold, provider-error, cancellation, and restart
  states with deterministic providers.
- Run TypeScript checks, browser tests/build, the complete Python suite, process
  smoke, container build, and installed-container smoke.
