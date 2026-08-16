# ADR 0033: A persona's reply may rank retained pictures, and nothing more

- Status: accepted
- Date: 2026-08-16
- Owners: Nice Assistant maintainers

## Context

A persona writes its reply before anything decides which picture is attached, so
it can describe walking the dog while a beach photo arrives beside it.

The obvious fix - plan the picture before the persona speaks - was put to the
owner on 2026-08-16 and declined. It would add a Task Model call to the critical
path of every turn, including turns containing no picture, and real-time voice
conversation is the direction the product is heading. That decision is recorded
as A12 in `docs/autonomous-decision-log.md`.

This fixes the same problem from the other end, at no per-turn cost. Capability
planning already runs after the assistant message commits, so the reply is
already written by the time a picture is chosen.

It reads as a contradiction of ADR 0017, which states that persona prose must
never be able to introduce or widen a media subject. The reason the two are
different is the whole argument, so it is written down rather than assumed.

## Decision

- When a request is served from the retained library, the persona's reply may
  reorder the candidates. It may not change which candidates there are.
- The candidate list is built exactly as before: from the user's own words,
  through the scene the platform planned, filtered by the same match threshold
  and the same never-twice-in-one-conversation rule. A picture that would not
  have been eligible is still not eligible.
- Affinity is counted as shared words between the reply and a stored picture's
  scene. It orders candidates; it is never compared against a threshold and can
  never promote something that failed one.
- What is read is the persona's recent replies in that chat, not the reply on
  the turn making the request. ADR 0021 replaces persona prose with a neutral
  platform acknowledgement whenever a message passes the image-action gate, and
  passing that gate is the only way a conversational picture request survives
  planning. The reply on the asking turn therefore never contains anything to
  rank by. The words that matter came earlier: the persona said it walked the
  dog, and then a picture was asked for.
- Three replies. Enough to catch "I got my nails done" one turn before "send me
  a picture", short enough that a conversation which has moved on is not still
  voting.
- The text is read when a queued request is submitted. It is not written onto
  the capability request, and it never reaches a task model. `planning_context`
  remains user messages only, and the test that proves persona prose cannot
  reach planning is unchanged.
- With no chat - a direct action, a background picture, a photo set frame -
  every affinity is zero and the order is the one it always was.

## Why this does not weaken ADR 0017

ADR 0017 exists because a persona that can say "here is a portrait of me on a
beach in a red dress" must not thereby cause a beach picture to be generated, or
widen an existing request into one. Both of those are about creating work.

Choosing between pictures that already exist creates nothing. The set was fixed
before the reply was read, by the user's request. The strongest thing a persona
can do here is pick the one of its own existing pictures that best matches what
it just said, which is the behaviour a person would expect and the opposite of
inventing a subject.

If the candidate set is empty, the reply changes nothing, because there is
nothing to order.

## Alternatives considered

- Plan the picture before the reply. Declined by the owner on latency grounds;
  see A12.
- Run the deterministic action gate first and plan early only for messages it
  flags. Also declined for now: the owner does not expect a keyword filter to
  catch requests made by hint and suggestion, and one that quietly misses is
  worse than none.
- Compose a separate caption after the picture is known. Rejected here because
  it needs a new task-model role and still leaves the reply itself describing
  something else.
- Let the reply widen the scene used for matching. Rejected: that is exactly
  what ADR 0017 forbids, and it would let prose reach pictures the user never
  asked about.
