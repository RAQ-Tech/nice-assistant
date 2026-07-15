# ADR 0007: Permissioned, durable capability requests

- Status: Accepted; planning portion superseded by ADR 0008
- Date: 2026-07-14

## Context

Image and video actions were previously selected by string heuristics and an
undocumented `<generate_image>` response tag. That made ordinary conversation
text capable of starting provider work, coupled prompt wording to routes, and
could not provide durable consent or audit history. The existing visual-identity
helper was unused scaffolding and did not establish identity persistence.

Nice Assistant also needs a future platform-level Task Model and media planner,
but using the persona model as that planner would recreate the same coupling.

## Decision

- A small typed `CapabilityRegistry` initially exposes only image and video
  generation. Tool arguments express semantic intent (`prompt`) and cannot
  select a provider, model, LoRA, workflow, or privileged setting.
- A model-issued tool call completes the text turn and creates a durable
  `pending_confirmation` capability request. No media job exists until the user
  approves it.
- Direct user actions, such as the existing image button, enter the same service
  with `permission_mode=explicit` and may queue immediately.
- Capability requests and append-only audit events are owner-scoped. A linked
  job and capability move together through legal states. Cancellation is
  idempotent, queued work is removed, and late provider artifacts are discarded.
- Repeated explicit requests can use `Idempotency-Key`; reusing a key with a
  different request is a conflict.
- Safe capability outcomes are included as tool results in later conversation
  context. Provider errors are not assistant messages.
- Tools are offered only when the corresponding provider is configured and an
  application adapter exists. Keyword detection and hidden response tags are
  removed.

## Consequences

ADR 0008 moved autonomous capability planning out of persona-model tool calls
and into a separately configured typed platform role. The permission, durable
request, audit, idempotency, cancellation, and execution decisions in this ADR
remain in force. The UI's explicit media buttons remain available; Nice
Assistant does not fall back to ambiguous text heuristics.

This is an execution and permission boundary, not the media intelligence layer.
Task Models, the media model catalog/planner, persona identity persistence, and
identity-aware workflows remain separate roadmap steps. Provider cancellation
is cooperative, so an external service may continue computing after Nice
Assistant has cancelled and discarded its eventual result.
