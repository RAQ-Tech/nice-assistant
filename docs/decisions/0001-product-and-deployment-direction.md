# ADR 0001: Voice-first private-LAN architecture

- Status: accepted
- Date: 2026-07-12
- Owners: Nice Assistant maintainers

## Context

The existing browser assistant contains chat, memory, speech, and media features,
but it is a scaffold whose synchronous and provider-specific boundaries cannot
deliver a convincing realtime companion safely.

## Decision

Nice Assistant will be a voice-first companion for private-LAN use. It will
support natural turn-taking with push-to-talk fallback. Speech providers are
hybrid: the quality-first primary may be cloud-hosted, while local engines run as
separate LAN services. Images and video remain modular supporting capabilities.

The application will migrate to typed `/api/v1` HTTP contracts plus an
authenticated realtime WebSocket. It owns conversation state and provider
policy, but not external services' model residency.

## Alternatives considered

- Text-first foundation: rejected because it would postpone the primary product
  experience and hide latency/streaming constraints.
- Local-only speech: rejected because current quality does not meet the product
  bar and the architecture can retain a private fallback without limiting the
  primary voice.
- Direct public-internet deployment: rejected because it expands security and
  abuse requirements beyond the intended trusted-LAN product.

## Consequences

The backend and browser require substantial modularization. Voice providers must
pass a blind evaluation before integration. Mobile microphone deployment needs
HTTPS. Media feature expansion is deferred until the voice core is accepted.

## Verification

The decision is complete when the roadmap's voice, hardening, and real-deployment
acceptance steps pass and their measurements are recorded in operations docs.
