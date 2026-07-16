# ADR 0021: Reply critical path and reviewable manual memory

- Status: accepted
- Date: 2026-07-16
- Owners: Nice Assistant maintainers

## Context

Persona text was durably generated before title, capability-planning, and memory
work, but some of those nonessential tasks still shared one sequential follow-up
job. A slow Task Model could therefore delay reconciliation or another unrelated
platform task. The chat memory action also promoted an assistant message directly
to active factual memory, even though assistant prose is not evidence that the
fact is true. Default chat chrome exposed model, workspace, memory, and diagnostic
state during every ordinary conversation.

## Decision

- After the assistant message commits, title generation, capability planning,
  and memory extraction run as independent durable jobs. Title and capability
  IDs are returned in `followup_job_ids`; memory retains its named extraction ID,
  and `followup_job_id` remains a compatibility alias.
- Follow-ups from one chat retain the chat ordering key. They are failure-isolated
  records but do not concurrently mutate the same SQLite conversation state;
  title is submitted before capability planning and memory extraction.
- The visible persona reply never waits for those jobs. Each follow-up may fail
  without changing a completed assistant turn, while causal ordering is preserved
  because no follow-up is created before the assistant message commits.
- For a clear image action, persona output is buffered and checked before any
  delta is published. Claims that an image was sent, taken, attached, matched, or
  verified are removed until durable platform evidence exists.
- The chat memory action opens an editable proposal and posts it to
  `POST /api/v1/memory-proposals`. The record starts `pending`; only review can
  make it active. The existing explicit memory-management API remains available
  for deliberate operator-created active facts.
- The default chat header and controls show the persona, conversation, speech,
  and blur essentials. Workspace, model, memory, state, and visualization controls
  remain available in an authenticated progressive-disclosure section.
- A visible conversation Cancel action exists only while its owned turn is queued
  or running. Media attachments retain their own cancel controls only while their
  linked media work can be canceled.

## Alternatives considered

- Keep one sequential post-reply job. Rejected because independent optional work
  should degrade independently and must not delay title or capability state.
- Trust the persona prompt to avoid premature media claims. Rejected because
  model instructions are not a durable evidence boundary.
- Save a whole assistant message as active memory and let the user edit it later.
  Rejected because unreviewed prose must never enter future prompts as fact.

## Consequences

Job consumers must reconcile a list of follow-up IDs and retain compatibility
with older single-ID results. Explicit image turns publish one guarded delta after
generation instead of raw token streaming; ordinary text turns continue to
stream. Manual proposals add no schema because Memory v2 already supports pending
manual records and audit events.

## Verification

- API tests prove independent title/capability execution, completed-turn survival
  under degraded Task Models, and pending-only manual proposals.
- Scenario tests prove long-context bounds, correction review boundaries, persona
  switching, scoped memory, truthful media wording, reload-safe media, fallback,
  and provider degradation.
- Browser tests prove edited proposals, progressive controls, truthful Cancel
  state, title reconciliation, Kokoro cleanup/interruption, and compact attachment
  behavior.
