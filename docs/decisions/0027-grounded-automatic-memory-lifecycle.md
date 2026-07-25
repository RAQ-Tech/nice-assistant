# ADR 0027: Grounded automatic memory lifecycle

- Status: proposed
- Date: 2026-07-25
- Owners: Nice Assistant maintainers
- Related: ADR 0005, ADR 0021

## Context

ADR 0005 made automatic extraction review-first: every extracted candidate is
pending until a person approves it. That prevented silent promotion of model
output, preserved provenance, and established audited memory states. ADR 0021
kept extraction outside the visible-reply critical path and made chat-originated
manual memory an editable proposal instead of treating assistant prose as fact.

Those safeguards remain necessary, but sending every candidate to review makes
routine memory depend on continual Settings maintenance. It also does not stop a
poor extractor from filling pending review with vague, irrelevant, unsupported,
or fabricated snippets. A confidence number alone cannot establish that text is
supported by the conversation, safe to retain, current, or visible to the right
persona.

The product needs a precision-first automatic policy that can eventually
activate clearly supported memories without making the model an access-control
authority. It also needs to distinguish enduring facts from time-bounded facts
and evolving projects or commitments. This ADR is a proposed replacement for
the automatic-candidate review boundary in ADR 0005 and a proposed extension of
ADR 0021's confirmation boundary. It does not describe behavior that is
currently shipped.

## Decision

### Admission and evidence

Automatic extraction will remain asynchronous and will run only after the
visible turn and its source messages have committed. Every admitted memory
candidate must retain exact provenance to the supporting owner utterance or
explicitly accepted decision, including its chat, persona, access context, turn,
and source message. Assistant text, hidden reasoning, rejected alternatives,
tool output, and generated summaries are not independent evidence of an owner's
personal fact.

Before a candidate can enter the memory lifecycle, platform-owned validation
will:

1. validate its bounded schema and source references;
2. verify against the full cited source context that the proposed statement is
   entailed without changing speaker, subject, quotation, negation, uncertainty,
   modality, time, or third-party attribution;
3. reject the credential and authentication material prohibited by ADR 0026;
4. reject unsupported, speculative, conversationally incidental, or malformed
   output; and
5. classify the candidate's semantic kind and currentness metadata.

The model may propose content, evidence, classification, and confidence. It may
not assign access. The platform will attach origin and source-persona-only
access, including for chats inside a business workspace. Any broader grant is a
separate, confirmed access operation.

The extractor cannot validate its own grounding. Automatic qualification
requires either an allowlisted extractive rule whose transformation is
deterministically checked or a separately versioned verification boundary that
sees the complete source context and candidate. A verifier must not accept the
extractor's confidence or rationale as evidence. If independent entailment is
unavailable or ambiguous, the candidate cannot become active.

### Memory kinds and currentness

The audited record lifecycle from ADR 0005 remains distinct from a memory's
semantic kind:

- **Durable** memories represent facts expected to remain true until corrected
  or deleted.
- **Temporal** memories represent facts with a required `valid_until` value.
- **Stateful** memories represent projects, commitments, and other evolving
  subjects. Where applicable, they carry a lifecycle value of `active`,
  `completed`, `cancelled`, or `superseded`.

Every v3-admitted memory will retain `last_confirmed_at` metadata derived from
the owner's supporting evidence, regardless of review or currentness state.
Legacy records without trustworthy confirmation evidence retain an explicit
unknown value rather than an invented date and cannot silently become current.
Expired temporal memories and stale or non-active stateful memories will be
excluded when assembling current factual context. They may remain in history
for provenance until the owner requests deletion under the product's deletion
contract.

A clear owner correction may automatically create a new evidence-linked
revision and mark the old record stale or superseded. Correction never rewrites
the old record's content or provenance in place and never changes grants,
forgets or deletes a record, or writes the universal owner profile. Ambiguous
corrections remain reviewable instead of silently changing current context.

### Automatic disposition

Pending review will be exceptional rather than the destination for every
candidate:

- A clearly stated, evidence-supported candidate that passes the proven
  automatic policy may become active.
- A plausible, grounded candidate that is materially ambiguous or needs human
  judgment may become pending.
- Weak, unsupported, malformed, prohibited, or merely transient output is
  rejected before admission. Content-free telemetry may retain a bounded reason
  code and count, but it does not create a memory or pending item.

Pending candidates do not expire or become deleted automatically in version
one. The interface may rank them and warn that review has accumulated, but no
confidence score may be the sole authority for activation, rejection, deletion,
or access expansion. Confidence is diagnostic input for ranking, evaluation,
and policy measurement. Independent semantic qualification and an allowlisted
automatic-eligibility class are mandatory for activation regardless of
confidence. Confidence may rank or break ties among already qualified records;
it cannot move an ambiguous candidate from pending to active.

The first release will use one carefully evaluated system-wide automatic policy.
Per-persona extraction policies are deferred until the common policy has
demonstrated dependable behavior.

### Deliberate and reference knowledge boundaries

The universal owner profile is separate from ordinary memory. Automatic
extraction must never promote a fact into that profile. Only onboarding or an
explicit, confirmed universal instruction may add or change its deliberately
shared fields.

Document assertions are reference knowledge, not personal memories. Documents
may be indexed, summarized, classified, and cited without entering this
lifecycle. A document-derived fact can become memory only after the owner
explicitly promotes the exact assertion or independently restates it in the
owner's own words. A short assent may establish one clearly presented decision
while discussing a document, but it cannot promote the factual claims in a
document or multi-claim summary.

Natural-language instructions to remember, forget, share, revoke, or make
information universal are detection inputs, not administrative authority. The
platform will present an exact proposed operation, including the content,
affected records, and resulting access, and will perform no mutation until the
owner confirms it. Cancellation or dismissal leaves memory and access
unchanged. A confirmed proposal supplies the explicit review boundary for the
displayed operation and is recorded in the audit history.

Mixed instructions are split by effect. For example, a factual correction may
create or supersede ordinary memory automatically, while a forget, delete,
share, revoke, or universal-profile effect remains an exact unexecuted proposal
until confirmation. Classification as a correction can never bypass an
administrative confirmation.

### Rollout boundary

Automatic activation remains disabled while the new policy runs in shadow mode
against a representative, human-labeled evaluation set. Predeclared quality and
privacy gates must be met before activation is enabled. Until then, current ADR
0005 review-first behavior remains authoritative in production.

When this ADR is accepted, implemented, verified, and shipped, it supersedes
only ADR 0005's rule that every automatic candidate must remain pending. ADR
0005's audited states, provenance, reversible forgetting, revision history, and
active-only retrieval continue to apply. It extends ADR 0021 by replacing chat
instructions that create an unexamined pending record with an exact confirmation
proposal; its post-reply isolation and prohibition on treating assistant prose
as evidence remain in force.

## Alternatives considered

- **Keep every automatic candidate pending.** This remains the safe production
  fallback until the new policy passes its gates, but it makes ordinary memory
  dependent on routine maintenance and preserves low-quality extraction noise.
- **Activate above a confidence threshold.** Rejected because a model can be
  confidently unsupported, assign the wrong kind, miss expiration, or propose
  inappropriate access.
- **Let the extractor choose scope and grants.** Rejected because access control
  is a deterministic platform responsibility and model output must not broaden
  disclosure.
- **Prefer recall and let users correct mistakes later.** Rejected because an
  incorrectly recalled private fact can damage persona continuity or disclose
  information before the user has an opportunity to repair it.
- **Overwrite a memory when corrected.** Rejected because it destroys
  provenance and makes it impossible to explain when or why current context
  changed.
- **Mine uploaded documents into personal memory automatically.** Rejected
  because reference material may contain third-party assertions, templates, or
  facts that the owner has never adopted.

## Consequences

The design reduces routine review while retaining a human path for genuine
ambiguity. Current context becomes more truthful because temporal expiration,
state transitions, and correction history are explicit. Source-persona-only
defaults and confirmation-gated access changes prevent extraction quality from
becoming an authorization boundary.

Implementation requires schema and migration work, evidence-preserving
extraction, a platform validation layer, currentness-aware retrieval, exact
confirmation UI, audit events, and a maintained labeled evaluation corpus.
Preserved revisions, rejected audit records, and non-expiring pending candidates
will consume storage. Precision-first policy will intentionally miss some facts;
that is preferable to silently retaining unsupported ones.

Existing memory must be exported and migration behavior verified before any
reset or cutover. Persona definitions and instructions are outside the reset
scope. Product disclosures about sensitive information remain useful, but they
do not replace secret rejection, access isolation, deletion safety, or testing.

Because this ADR is proposed, documentation and interfaces must continue to
describe the current review-first behavior until the corresponding phase is
implemented and verified. Acceptance of this design alone must not be presented
as automatic memory being available.

## Verification

Implementation is not complete until:

- schema and migration tests prove kind, `valid_until`, `last_confirmed_at`,
  stateful lifecycle, evidence, revision, and audit fields survive restart and
  legacy migration without inventing confirmation dates for ungrounded legacy
  rows;
- extraction tests prove that every admitted automatic memory is entailed by
  its cited owner evidence and that assistant alternatives, hidden reasoning,
  summaries, tool output, malformed records, and prohibited credential or
  authentication material cannot become personal facts;
- grounding tests cover quotation, speaker and subject changes, negation,
  uncertainty, modality, third-party attribution, and misleading exact-span
  matches; extractor self-confidence never satisfies the independent verifier;
- access tests prove that the platform, not model output, assigns
  source-persona-only access and that no extraction result can broaden it;
- retrieval tests exclude expired, stale, completed, cancelled, forgotten, and
  superseded records from current facts while preserving authorized history;
- correction tests prove that clear corrections create new revisions, preserve
  old content and evidence, and update currentness without destructive
  overwrite;
- owner-profile and document tests prove that neither automatic extraction nor
  document indexing can promote ordinary or universal personal memory;
- interaction tests prove that natural-language administrative instructions
  show the exact proposed mutation, cancellation changes nothing, confirmation
  applies only what was displayed, and the event is auditable;
- mixed-intent tests prove automatic correction preserves grants and profile
  data while forget, delete, share, revoke, and universal effects remain
  confirmation-gated;
- pending-lifecycle tests prove that version-one candidates never expire,
  activate, reject, broaden access, or delete solely because of age or numeric
  confidence; and
- shadow evaluation on a versioned, representative, human-labeled corpus meets
  predeclared precision, unsupported-activation, classification, correction,
  secret-rejection, and access-isolation thresholds before automatic activation
  can be enabled.

Focused tests, the complete suite, a process or container smoke, and live
deployment acceptance must then prove that conversation completion remains
independent of extraction failure and that the shipped interface and
documentation describe the actual rollout state.
