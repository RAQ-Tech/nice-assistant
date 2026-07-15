# Product vision

Nice Assistant is a private-LAN, voice-first companion that should feel like a
coherent presence rather than a collection of provider demos.

## Core experience

- Speech should sound credible, emotionally appropriate, and consistent with
  the selected persona.
- A conversation should support automatic end-of-turn detection, low-latency
  responses, cancelable playback, and interruption by the user.
- Push-to-talk remains available when hands-free mode is unsuitable or degraded.
- Memory should improve continuity without silently collecting noise or flooding
  the model context.
- Cloud providers may deliver the best primary experience. Separately deployed
  local LAN providers supply privacy and outage fallbacks.

## Supported boundary

The product targets trusted household or small-team use on a private LAN. It may
sit behind an HTTPS reverse proxy or VPN, but direct public-internet exposure is
not a supported deployment model.

## Supporting capabilities

Images, video, and future tools are permissioned capabilities. They remain
modular, observable, cancelable, and subordinate to the voice/conversation core.
Cross-persona background decisions belong to platform Task Models with typed,
evaluated contracts; an individual persona model must not silently own provider,
model, LoRA, workflow, or resource selection.
Task Models describe intent, while deterministic platform policy chooses media
resources from operator-reviewed metadata and shows that plan before approval.

When a persona presents generated media as depicting them, visual identity must
come from a durable, user-reviewable identity profile and validated references.
Prompt hints alone are not identity persistence, and failed identity validation
must be reported or retried rather than shown as if it were the persona.

## Product quality bar

The UI and documentation must distinguish working, degraded, disabled, and
unimplemented behavior. A feature is complete only when its runtime effect,
failure behavior, tests, and operational requirements are all verified.
