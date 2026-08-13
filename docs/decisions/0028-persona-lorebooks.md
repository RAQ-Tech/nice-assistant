# ADR 0028: Persona lorebooks

- Status: accepted
- Date: 2026-08-11
- Owners: Nice Assistant maintainers

## Context

The character card (ADR 0026) is always present, so everything in it competes for
budget on every turn. Background detail does not need that treatment: a persona's
sister, apartment, or job matters when the conversation is about them and is dead
weight otherwise. Paying for all of it on every turn is what makes authored depth
expensive.

`docs/persona-depth-spec.md` phase 3 proposed keyword-matched injection as the
answer. It is the last phase of that spec, and the one whose usefulness most
depends on being able to see what fires.

## Decision

- `persona_lore_entries` stores owner-scoped, persona-scoped entries with a title,
  trigger keys, optional secondary keys, content, `always_on`, `case_sensitive`,
  `priority`, and `enabled`. Scope is the persona rather than the workspace,
  matching how memory scope now behaves; workspace sharing can follow if it turns
  out to be wanted.
- Matching is deterministic and platform-owned. No model chooses which entries
  fire, consistent with the rule that task models cannot select media resources
  and cannot set memory scope.
- **Keys are literal strings, never patterns.** Operator-authored regex is both a
  footgun and a denial-of-service surface. A stored key of `.*` matches the text
  `.*` and nothing else.
- Matching uses non-word lookarounds rather than `\b`, so `sister` does not fire on
  `sisterhood` while a key like `St. Clair` still matches.
- **The scan window is the current message plus the last three transcript
  messages.** An entry should fire because the topic is live, not because it came
  up an hour ago.
- **No recursion.** Injected lore is not itself scanned. Recursive activation is
  where lorebooks become unpredictable and impossible to budget.
- Fired entries sort by priority, then recency, then id, and are included whole or
  skipped — never truncated mid-entry. A skipped entry does not end selection, so a
  single oversized entry cannot waste the rest of the allowance. This is the rule
  the saved-memory selector already uses.
- Lore is a droppable data section under `ContextPolicy.lore_ratio` (0.12), placed
  between example dialogue and saved memory, and third in the yield order
  established by ADR 0027: summary, saved memory, lore, then example dialogue.
- `POST /api/v1/personas/{id}/lore/preview` answers which entries a given message
  fires and which of them fit. It ships with the feature rather than after it.

## Alternatives considered

- Semantic or vector retrieval over background detail. Rejected: it adds a service
  and an embedding model to a GPU budget already under contention, and the spec's
  non-goals rule it out. Keyword matching needs neither.
- Allowing regex keys for power users. Rejected; see above.
- Scanning the whole transcript. Rejected because an entry would keep firing long
  after the subject moved on, spending the allowance on stale detail.
- Recursive activation, as some lorebook implementations support. Rejected because
  the budget stops being predictable and the failure is hard to diagnose.
- Shipping the preview route later. Rejected. Without a way to paste text and see
  what fires, keyword tuning is guesswork, and a feature that cannot be tuned gets
  abandoned.
- Truncating an oversized entry to fit. Rejected: half a fact is worse than no
  fact, and the entry list already lets the operator split it.

## Consequences

Lore costs nothing on turns that do not mention it, which is the whole point: a
persona can carry far more authored background than the budget would allow if it
were all always-present. Turning an entry `always_on` opts it back into that cost,
and the editor says so.

Matching quality is now an authoring concern rather than a model concern. That is
a deliberate trade: it is predictable and debuggable, but it will miss a paraphrase
that shares no keyword. The preview route is what makes that tractable.

The lore section rides on the ADR 0027 history floor, so adding entries cannot
starve the conversation regardless of how many fire at once.

This completes `docs/persona-depth-spec.md`. Its open question 2 — whether lore
should be shareable across personas in a workspace — is answered as proposed:
persona-scoped, with sharing left as a later addition.

## Verification

- Python tests prove word-boundary matching, case sensitivity, literal-key
  handling of pattern-looking text, punctuation keys, secondary keys as an
  additional requirement, `always_on` without keys, an entry with no keys never
  firing, the bounded scan window, an entry falling out of that window, injected
  lore not triggering further entries, priority and recency ordering, whole-entry
  inclusion, a skipped entry not blocking smaller ones, key parsing bounds,
  CRUD round-trip, rejection of an entry with neither keys nor `always_on`,
  preview contents, disabled entries being excluded from preview and from turns,
  cross-persona entry access, owner isolation on every route, and a fired entry
  reaching the provider while a quiet one does not.
- Browser tests prove entries load only when the section is opened, the firing
  summary, always-on and disabled labelling, keyword list editing, preview results
  including what did not fit, the empty-preview message, a rejection surfacing, and
  deletion removing the entry.
