# ADR 0039: A memory can be found by a question that shares none of its words

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

Memory retrieval was full-text keyword search plus recency. "What do I drive"
never found "owns a 2019 Tacoma", because the two share nothing to match on. The
memory was there, correct, approved, and invisible.

The owner had previously refused semantic retrieval for lore, on two grounds
that were right: it optimises recall when the complaint was noise, and an
embedding model competes for a GPU that is already contended. Those grounds do
not transfer intact to memory. Memory recall is not noisy - the extraction
pipeline and the approval step handle that - it is simply blind to paraphrase.
And the cost turns out to be tiny, once measured rather than assumed.

An earlier version of this decision was recorded as an overnight scheme that
expanded each memory into extra search terms, because the question put to the
owner presented the per-turn embedding as a real trade-off. It is not one. An
embedding model is a few hundred megabytes against four to five gigabytes for
the chat model, and embedding one question is single-digit milliseconds against
a turn that already takes seconds.

## Decision

Real vectors, compared by direction, with keyword search kept in front.

**Three sources, in priority order.** An exact keyword match wins, because
somebody who names a thing means that thing. A vector match comes next, which is
what finds the paraphrase. Recency fills the rest, which is what happens when
neither has an opinion.

**Normalised on write, so retrieval is a dot product.** There is no numeric
library here and none is wanted. Every vector is scaled to unit length when
stored, which removes the square roots and the per-query normalisation, and the
candidate set is bounded, so the cost is fixed rather than growing with how much
the assistant remembers.

**A weak match is dropped, not ranked last.** Anything below a similarity floor
does not appear at all. A tenuous memory in a context window is worse than no
memory, because the model reads whatever is there as relevant - which is the
noise complaint that made the owner refuse this for lore.

**The reply path never goes looking for the model.** It asks only once a
background pass has actually reached it. A deployment that never pulled an
embedding model pays nothing per turn - not a failed connection, and above all
not a timeout. If the model disappears mid-life, one failure stops it being
asked again until a background pass finds it.

**Vectors are computed in the background, never when a memory is written.**
Approving a fact should not wait on a model, and a model that is down should not
stop somebody approving one. The same background thread that produces scenes
does this, but unlike scene production it runs whether or not pre-generation is
switched on: recall must not quietly depend on a picture setting.

**A vector from a different model scores zero rather than raising.** Vectors of
different dimensions are not comparable. One stale row must not break a whole
retrieval, and the model name stored beside each vector is how a stale one is
recognised and recomputed.

## Alternatives considered

**Expand each memory into search terms overnight, and keep retrieval purely
lexical.** This was the first recorded decision. It has genuinely zero reply-path
cost, and it is guessing in advance what somebody might ask rather than comparing
what they did ask. Reversed once the actual cost was measured instead of
estimated.

**Adopt mem0, Zep, Letta, or Cognee.** Refused, and still refused. Each adds a
service and its own model to a contended GPU, and none provides the per-persona
boundaries this already has. What was wanted here was about two hundred lines and
one small model.

**Embed when a memory is written.** Simpler to reason about and it puts a
provider call inside a user-facing save. A person approving a fact would wait on
a model, and a model being down would stop them.

**A single vector index rather than a bounded scan.** Worth it at a scale this
will not reach. A few hundred comparisons of a few hundred floats is
microseconds; an index is a dependency, a build step, and a thing that can be
stale.

## Consequences

A persona can be asked something in words it has never seen and still remember.
That is the whole point, and it is the closest thing on the list to the goal the
owner keeps restating: that the persona should feel like it knows them.

The embedding model runs on the same machine as everything else. No conversation
text leaves it, which is what `docs/task-models.md` already commits to for the
task roles.

The similarity floor and the candidate ceiling are constants chosen from
reasoning rather than from this deployment. They are the two numbers most likely
to need tuning once there is a real memory set behind them, and they are
deliberately in one module where that is a small change.
