# ADR 0023: Persona image-send permissions

- Status: accepted
- Date: 2026-07-17
- Owners: Nice Assistant maintainers
- Supersedes: the `always_ask` image path in ADR 0019

## Context

Nice Assistant is meant to feel like a private conversation, not a capability
approval console. A person who explicitly asks for a picture has already given
permission for that ordinary image action. Asking for the same permission again
made a basic feature disruptive, and an older installed revision proved that
browser-only post-approval presentation could also lose the picture on reload.

The operator still needs a simple way to decide whether a particular persona
may fulfill picture requests. That permission must not authorize unsolicited
generation or weaken the conservative intent boundary.

## Decision

- Ordinary image requests never enter `pending_confirmation`. A high-confidence
  explicit user image action is admitted, audited, and queued automatically.
- Each persona has a persisted `allow_image_sends` preference, defaulting to
  true. When false, the image capability is withheld from that persona's
  conversational Task Model and the persona is instructed not to promise a
  picture.
- The persona preference applies only to conversational fulfillment. A direct
  user image action remains available.
- Stories, discussion, explanations, hypotheticals, and quoted instructions do
  not create image work. The persona preference does not authorize proactive or
  unsolicited image generation.
- Video and future consequential capabilities remain confirmation-gated.
- A strict identity or readiness block becomes a compact failed, retryable
  picture attachment. It never falls back to a per-image approval card.
- Migration `0018_human_image_delivery` removes the retired global image
  confirmation setting and converts surviving pending image approvals into
  cancelled, retryable records. Pending video approvals are preserved.

## Alternatives considered

- Keep `always_ask` as an advanced global preference. Rejected because it
  repeats explicit consent and keeps a disruptive, failure-prone path in the
  core picture-message experience.
- Use only one global image enable switch. Rejected because the operator asked
  for persona-specific control and direct user actions have a different
  authority boundary.
- Let an enabled persona send images without explicit user intent. Rejected
  because the toggle is permission to fulfill requests, not authority to invent
  paid or resource-intensive work.

## Consequences

Capability permission mode `auto` remains an audited platform decision rather
than model autonomy. Existing personas receive the compatibility-preserving
true default. Older clients may omit the field; updates preserve its stored
value unless it is explicitly supplied. Approval and denial endpoints remain
for video and future confirmation-gated capabilities.

## Verification

- API and migration tests prove the persona default, update round-trip, retired
  setting removal, pending-image conversion, and pending-video preservation.
- Capability tests prove enabled-persona auto-run, disabled-persona
  suppression, direct-action availability, automatic retry, and the unchanged
  negative intent boundary.
- Browser tests prove the persona control persists and no image approval card
  appears.
- Installed acceptance requests a picture through an enabled persona, verifies
  exactly one durable attachment after reload, then proves a disabled persona
  does not start conversational image work.
