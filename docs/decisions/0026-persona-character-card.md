# ADR 0026: Persona character card in the protected prompt section

- Status: accepted
- Date: 2026-08-11
- Owners: Nice Assistant maintainers

## Context

A persona carried `system_prompt`, `personality_details`, and five numeric traits.
Nothing in that model described how a persona actually speaks or holds together
across a conversation, so voice consistency depended on prose the operator had to
restate inside a free-text field. `docs/persona-depth-spec.md` proposed authored
character material as the answer, in phases; this ADR records the first phase.

The material has to be present on every turn to do its job, which puts it in the
protected prompt section. That section is never clipped: `ContextService.plan()`
raises `context_too_large` and fails the turn rather than quietly dropping part of
who is speaking. An unbounded card therefore converts an authoring mistake into a
turn failure at the worst possible moment.

## Decision

- Personas gain four nullable card fields — definition, personality, style, and
  behavior. They are additive: a persona that leaves them empty renders exactly
  the prompt it rendered before, and no existing content is migrated.
- The card is rendered as labelled lines inside the existing persona instruction
  block, above `personality_details` and below the identity and trait lines. The
  persona `system_prompt` stays last, because the settings surface describes it as
  the highest-priority persona instruction and that ordering is what operators
  have configured against.
- The card is capped when it is saved, not when a turn is planned.
  `PUT /api/v1/personas/{id}/card` rejects an oversized card with 422 and
  `persona_card_too_large`, and the message names the estimate, the cap, the
  prompt budget, and the context window it was derived from.
- The cap is `ContextPolicy.card_max_ratio` (0.30) of the prompt budget, computed
  with the same output and safety reserves `plan()` subtracts. It is taken against
  the **narrowest** context window the account has configured — the account default
  and every per-model override — because a persona is not bound to one model.
- Card fields are writable only through the card route. The general persona route
  rejects them, so the cap has exactly one enforcement point.
- Persona responses carry `card_token_estimate` and the three budget numbers, so
  the editor can price a card while it is typed instead of failing on save.
- `TokenEstimator` and `ContextPolicy` moved to `app/context_policy.py`. Prompt
  assembly in `app/chat.py` now shares the estimator, and `app/task_contracts.py`
  already imports `app/chat.py`, so leaving them in `app/context_service.py` would
  have closed an import cycle.

## Alternatives considered

- Treat the card as droppable data alongside saved memory. Rejected because a
  persona whose character silently disappears under budget pressure is a different
  persona, and this codebase does not degrade identity quietly.
- Cap the card at turn time. Rejected because the failure would surface during a
  conversation, far from the edit that caused it, and `AGENTS.md` requires that a
  setting which saves successfully produces a working runtime.
- Derive the cap from the most generous configured window. Rejected because the
  persona would then break on the operator's smallest model.
- Add the example-dialogue column now, ahead of its phase. Rejected because a
  stored field that nothing reads reads as support that does not exist.

## Consequences

The cap is honest about the card's own share of the budget; it does not make an
arbitrarily long `system_prompt` or `personality_details` safe, and those remain
uncapped as before. Raising the account context allocation raises the cap, which
is the intended operator lever — at 4096 the allowance is 998 tokens, and at 8192
it is 2181. A per-request `context_window_tokens` override smaller than anything
configured is still a turn-time concern and outside what a save can guarantee.

The browser duplicates the label set and the estimator so counts update while
typing. `tests/test_persona_card.py` and `frontend/tests/persona_card.test.ts`
price the same card, so drift between the two breaks a test rather than showing
an operator a number the platform will not honour.

Example dialogue and lorebooks remain unbuilt. The spec's context-headroom
question is unresolved and gates those phases, not this one.

## Verification

- Python tests prove render order, empty-card neutrality, the budget arithmetic at
  4096 and 8192, the narrowest-window rule, the 422 message naming the budget,
  rejection leaving the stored card unchanged, owner isolation, refusal of card
  fields on the general persona route, and that a card saved at the cap plans and
  reaches the provider without `context_too_large`.
- Browser tests prove live per-field counts, the budget meter and its warning
  state, saving through the card route, and the rejection message surfacing.
