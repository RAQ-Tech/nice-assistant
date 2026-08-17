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

**A weak match is dropped, not ranked last, and only a few strong ones are
promoted.** Anything below a similarity floor does not appear at all, and at
most six memories are moved ahead of what is merely recent. A tenuous memory in
a context window is worse than no memory, because the model reads whatever is
there as relevant - which is the noise complaint that made the owner refuse this
for lore. The floor is measured; see below.

**The text is embedded as written.** nomic-embed-text documents `search_query:`
and `search_document:` prefixes for exactly this use. Measured here they made
recall worse - the gap between right and wrong answers narrowed, and a question
that had been answered correctly stopped being. Following the documentation
would have been the reasonable thing to do and the wrong one.

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

## What the floor actually is

This decision was first written with a similarity floor of 0.55, reasoned out
rather than measured, and flagged here as the number most likely to be wrong.
It was. Measured against a real nomic-embed-text with twelve ordinary memories -
a truck, a bass guitar, a sister's name, a coffee order - and nine questions
that deliberately shared no words with their answers:

- The right memory ranked first for eight of the nine questions.
- Right answers scored between 0.42 and 0.64, median 0.56.
- Wrong answers had a median of 0.36 and reached 0.50 at worst.

Which makes the floor a straight trade:

| Floor | Right answers kept | Wrong answers admitted |
| --- | --- | --- |
| 0.40 | 9 of 9 | 17 of 99 |
| 0.45 | 8 of 9 | 3 of 99 |
| 0.50 | 6 of 9 | 0 of 99 |
| 0.55 | 5 of 9 | 0 of 99 |

At the reasoned 0.55 the feature would have looked broken while every part of it
worked: four questions in nine finding nothing, including "what do I drive",
which is the example this whole decision was described by.

**0.40, with at most six matches promoted.** The floor is set where every right
answer survives, and the cap is what handles the wrong ones it lets through.
This is the right division of labour: a floor tight enough to exclude all noise
is also tight enough to exclude real answers, whereas a wrong memory that is
merely present costs little - unrelated memories are already there, put in by
recency. The floor decides what may be considered; the cap decides how much of
the context window meaning is allowed to claim.

These are measured on one deployment, one model, and a small set. They are
deliberately two named constants in one module.

## Consequences of that measurement

Two of the nine questions are worth naming, because they bound what this
feature can honestly be said to do.

"What do I drive" scores 0.42 against the truck - the weakest right answer in
the set, and it ranks second behind a memory about a bass guitar. It is above
the floor and it is found, but a set with more near-misses in it could push it
under. The honest claim is that meaning-based recall works and is not
infallible, not that paraphrase is solved.

The remaining number chosen from reasoning is the candidate ceiling. Unlike the
floor it is not a quality setting: it bounds work, and beyond it recency has
already decided.
