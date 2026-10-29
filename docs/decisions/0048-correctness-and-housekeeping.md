# ADR 0048: Correctness and housekeeping

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

Batch 6 of the 2026-10-27 project review: the small things that were wrong or
unchecked. Recall by meaning, added in ADR 0039, compared the question only with
the forty newest memories - the ones recency had already put in front of the
model - so an older memory could be found by an exact word and nothing else.
Every read of a finished turn's state left behind an event stream that only an
ended stream could ever be pruned from, so a page that opened old chats slowly
grew the server's memory. A reply that failed before the model said a word, such
as a dropped connection or Ollama answering 503 while it reloaded, failed the turn
though nothing had been shown. The linter checked only whether files parsed, and
nothing checked types; three dependency updates had waited since September.

## Decision

**Meaning is compared beyond what recency chose.** The question is compared with
the newest 400 stored vectors in scope, reading only identifiers and vectors.
That ceiling was always the intended one; the comparison is about 30
microseconds a vector, about 12 ms for all of them on a desktop CPU, and the
ceiling is what keeps it a fixed cost.

**Reading leaves no trace.** Reading a turn's text, or subscribing to a turn
that has finished, no longer creates an event stream. Only a turn still running
gets one, and it ends and is pruned as before.

**A failed start is tried once more.** A retryable failure before any text
arrived repeats the request once. Not after text has arrived, because what was
shown cannot be taken back; not after a timeout, which would double the longest
wait allowed; not after cancellation; and never more than once.

**Checks that find mistakes.** Ruff now checks every name is real and used (F)
and the common Python mistakes (B), with FastAPI's `Depends`, `File` and `Query`
declared as the deliberate defaults they are. mypy checks all of `app` at its
default strictness, and both run in the verifier and CI. Password digests are
compared in constant time.

**The three largest modules are split along real seams.** The `/api/v1` request
and response models are `app/api_models.py`; the routes stay in `api_v1.py`. The
picture side of the repository - media catalog, visual identity, and protected
artifacts - is `MediaRepository` in `app/media_repository.py`, a base class of
`ApplicationRepository`, so callers still use one `uow.repo`. Binding a
persona's identity into an image plan is `IdentityBinding` in
`app/media_identity_binding.py`, a base class of `MediaCatalogService`. `now_ts`
lives in `app/clock.py` and is still importable from `app.repositories`. Nothing
about behaviour changed.

**Dependencies are current.** The Python group (including SQLAlchemy 2.1 and
FastAPI 0.143), the browser group (including Playwright 1.64 and Vite 8.3), and
Vitest 5 are merged. Vitest is taken to 5.0.5: 5.0.0's type definitions import a
package it does not install, which failed the browser typecheck.

## Alternatives considered

- **Compare every stored vector.** Simplest, and correct for a while, but the
  cost then grows with everything ever remembered and lands before the first
  word of every reply.
- **Retry inside the Ollama adapter.** It would also retry the background task
  models, which have their own fallback order, and it could not see whether the
  reply had already shown text.
- **Strict mypy.** Most of the application's functions are unannotated; strict
  mode would have been an annotation project rather than a check. The default
  level checks every annotation that exists and every module added from now on.

## Consequences

A memory older than the newest 400 with vectors is still found only by keyword
or recency; that is the stated cost of a fixed ceiling. A reply whose first
attempt fails quickly now takes up to twice as long to fail when the second
attempt fails too. New code must pass mypy and the wider lint rules.

## Verification

- `tests/test_semantic_memory.py`: a memory older than forty-five newer ones is
  found by meaning, and a keyword match still comes first.
- `tests/test_turn_events.py`: a thousand reads of finished turns leave no
  streams; a subscriber can still wait for a turn that has not published.
- `tests/test_reply_path.py`: a failure before any text is tried once and only
  once; not after text, a timeout, or a refusal.
- `tests/test_module_contracts.py`: a wrong password and a malformed stored hash
  are refused.
- The verifier's static analysis and type check steps.
