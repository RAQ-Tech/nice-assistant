# ADR 0020: Durable picture-message attachments

- Status: accepted
- Date: 2026-07-16
- Owners: Nice Assistant maintainers

## Context

Generated images were represented partly by browser-only synthetic messages and
partly by large capability cards. Reloading could lose presentation state, a
single global busy phase prevented ordinary conversation during media work, and
failures exposed provider-plan machinery instead of behaving like a failed
picture message.

## Decision

- Every chat media request owns a durable `ChatAttachment` linked to its
  assistant message, capability request, and protected media artifact.
- Direct image actions and Task Model-planned image actions use the same
  capability lifecycle and attachment transcript contract. Browser state never
  invents a completed media message.
- Message responses include attachment lifecycle, identity state, safe error,
  retry availability, and protected content URL. Reload resumes queued/running
  attachments without creating another request.
- Ordinary chat shows compact progress, scoped cancel/retry, and collapsed
  details. The saved `chat_blur_images` preference defaults off; when on, first
  activation reveals and second activation opens the preview.
- Media work does not own the conversation, recording, or playback phase.
- When more than one enabled catalog image backend is explicitly configured,
  coordinator planning excludes candidates whose real provider check is not
  ready, then applies deterministic priority and compatibility rules. The
  selected provider remains visible in attachment Details.

## Alternatives considered

- Recreate attachment messages in the browser from completed jobs. Rejected
  because reload, multi-client use, cancellation, and retry would diverge.
- Keep one global pending request. Rejected because image latency must not block
  core conversation and Kokoro interaction.
- Put the full plan in every chat bubble. Rejected because diagnostics should be
  available without replacing the companion experience.

## Consequences

Migration `0017_chat_attachments` adds durable attachments, linked retries, and
the effective `auto` permission audit while preserving the legacy constrained
permission value for compatibility. Interrupted attachments become failed and
retryable at startup. Direct manual-provider requests remain truthfully marked
as manual plans; catalog-planned requests can select a ready fallback candidate.

## Verification

- Migration tests prove existing capability events, plans, jobs, settings, and
  messages survive upgrade.
- Contract tests prove automatic explicit intent, negative story/discussion
  intent, reload-safe completion, protected content, failure, and linked retry.
- Browser tests prove compact attachments, independent interaction, blur-off
  default, reveal-then-preview, scoped cancel, and retry.
- Installed-browser acceptance exercises reload and provider failure against the
  production deployment before milestone promotion.
