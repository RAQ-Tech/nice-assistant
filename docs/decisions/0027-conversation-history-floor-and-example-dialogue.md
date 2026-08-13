# ADR 0027: Conversation history floor and persona example dialogue

- Status: accepted
- Date: 2026-08-11
- Owners: Nice Assistant maintainers

## Context

`docs/persona-depth-spec.md` recommended deferring everything after the character
card until the deployment could afford an 8k context allocation. Its own budget
table shows why: at 4096, a card plus example dialogue plus a lorebook plus saved
memory plus a summary leaves roughly 431 tokens of conversation — two or three
messages. A persona that knows exactly who it is and cannot remember what was said
four messages ago is less coherent, not more.

That deferral treated a missing guarantee as a missing setting. Nothing in
`ContextService.plan()` protected the conversation at all: data sections were
assembled first and history filled whatever was left, so saved memory and a summary
could already crowd out the conversation before authored material existed. The
spec's own testing section asks for exactly the missing piece — "history is not
starved below a floor."

## Decision

- `ContextPolicy.history_floor_ratio` (0.25) reserves a share of the prompt budget
  for conversation history. When the assembled prompt would leave less than that,
  droppable data sections are removed until it does.
- Sections yield in reverse authority order: summary, then saved memory, then
  example dialogue. The summary goes first because it is history at lower fidelity,
  so trading it for verbatim recent turns loses the least.
- Nothing yields on a turn with no history, because there is no conversation to
  protect. Protected sections are never dropped; the card's save-time cap under
  ADR 0026 remains the only thing that bounds them.
- A turn that dropped sections is marked degraded with
  `history_floor_dropped:<names>`, and a dropped memory or summary section is
  reported as omitted rather than included. The turn does not claim to have used
  context it did not send.
- Personas gain `card_example_dialogue`: `<START>`-delimited exchanges with
  `{{char}}` and `{{user}}` substituted at render. It is a droppable data section
  under `ContextPolicy.example_ratio` (0.10), rendered above saved memory and
  labelled as voice examples rather than transcript.
- Exchanges are included whole or not at all, later ones dropped first. Half an
  exchange teaches a model nothing.
- Example dialogue is stored on the card route but is not under the card's cap. It
  cannot fail a turn, so rejecting a save would be a limit without a hazard. The
  editor instead reports how many exchanges fit today.

## Alternatives considered

- Defer example dialogue until 8k, as the spec recommended. Rejected because the
  floor removes the hazard the recommendation was protecting against, and the
  8k decision depends on hardware measurement that gates nothing here.
- Clip example dialogue to fit, like the summary. Rejected because a truncated
  exchange models broken speech.
- Reserve the floor before assembling data sections. Rejected because it would
  drop material even when the conversation is short enough not to need it; the
  loop only removes what the floor actually requires.
- Substitute the account username for `{{user}}`. Rejected because a username is
  an account credential, not a chosen display name.

## Consequences

Long conversations now keep recent turns that memory and summaries previously
displaced. That is a behavior change independent of persona depth, and it can make
a turn degraded where it silently truncated history before — which is the point:
the omission was already happening and was not being reported.

The floor is a share of the prompt budget, so it scales with the context
allocation rather than needing a second setting. At 4096 it reserves 832 tokens,
roughly five or six messages.

Example dialogue never reaches the summarizer, the memory extractor, or the
durable transcript: it is assembled into the persona prompt only, and a test
asserts platform roles do not receive it.

Lorebooks remain unbuilt. They add keyword matching and per-entry selection rather
than a single authored blob, and they are the phase whose value most depends on
the preview route.

## Verification

- Python tests prove nothing yields when the conversation already fits, that
  sections yield in reverse authority order, that only as many yield as the floor
  requires, that a first turn with no history keeps its context, that protected
  sections survive, block splitting and placeholder substitution, whole-exchange
  inclusion, omission of a single oversized exchange, substituted example dialogue
  reaching the provider, its absence from platform task prompts, and no example
  section for a persona without one.
- Browser tests prove block splitting, substitution, whole-exchange selection, the
  meter naming how many exchanges fit, and example dialogue saving with the card.
