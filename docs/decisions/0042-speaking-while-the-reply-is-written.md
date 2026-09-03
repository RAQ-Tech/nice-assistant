# ADR 0042: Speaking while the reply is still being written

- Status: accepted
- Date: 2026-09-03
- Owners: Nice Assistant maintainers

## Context

ADR 0037 made speech start on the first piece of audio rather than the last.
It removed the synthesis wait and left the writing wait: a persona says nothing
until the whole reply has been written, and then its first words arrive. On a
long reply that is most of the silence a person sits through, and the owner
noticed it during the acceptance walkthrough: "if there is a way to speed that
along it would be nice, so long as the speech never gets ahead of the writing".

ADR 0037 also rejected cutting a reply into sentences and synthesizing each,
because the product would be deciding where sentences end, and it would get
that wrong on exactly the replies people care about. That reasoning was sound
for the completed-file path, where the whole text was already there and a
single request would do. It does not hold once the text is still arriving,
because then there is nothing else to speak from.

## Decision

**Each finished sentence is spoken as the stream produces it, and the sound
never passes the text.** The browser watches the reply as it is written. When
a piece ends at terminal punctuation followed by a break - a space, a newline,
the end of a paragraph - and is at least a couple of dozen characters long, it
is sent to be spoken. A full stop at the very end of what has arrived is not
yet an ending: the writer may still be adding to that line. Code fences are
never cut into. Anything doubtful waits for the next break, so a doubtful cut
costs a bad seam and a late cut costs a moment. Whatever is left when the reply
finishes is the last piece.

By construction the sound cannot get ahead of the writing: a piece is only
spoken once its text is on the screen, and nothing is ever spoken that was not
first written.

**Pieces go, in order, into the one stream the audio element is already
playing.** Each is streamed the way ADR 0037 streams a whole reply, so its
first sound arrives before its last, and its bytes are appended to the same
media source as the piece before it. The element plays through; when the
writer is slower than the voice it waits for the next sentence, and the
seam between two pieces is the seam between two sentences, which is where a
pause belongs anyway. Only formats whose frames can follow one another in a
stream are eligible, which is the same set that could stream at all.

**A stop cuts every queued piece at once.** Stopping aborts the piece being
spoken, forgets every piece waiting behind it, and tells the server the session
is abandoned. Nothing queued plays afterwards, and nothing is stored.

**The stored recording is the whole reply.** The pieces belong to one speech
session on the server, which keeps what each piece produced and writes one
recording when the browser says the reply is finished. Replay plays that
recording, the same as before: the whole reply, once, without asking the
provider to speak it again. A session nobody finishes stores nothing.

**Only formats that can start early do this.** The same rule as ADR 0037: a
format that cannot be played incrementally speaks the completed reply exactly
as before. Nothing about that path changed.

## Alternatives considered

**Feed the provider the text as it arrives.** The speech services this product
talks to take their text up front; none of them accept a sentence appended to
a request already in flight. A session of pieces is the same idea made of
requests they do understand.

**Speak each sentence as its own completed file.** Simpler, and the first
sentence would still arrive early - but every later sentence would wait for
its whole synthesis, and the wait would return at each seam.

**Concatenate the pieces on the browser and store from there.** The browser
would have to upload what it had just downloaded, and a reply the browser
walked away from would leave the server to guess. Keeping the bytes where they
were produced is one copy fewer and one honest answer about what was stored.

**Store the sentence files separately and stitch them on replay.** Replay
would then depend on every piece surviving cache rotation together, and a
missing one would leave a hole in the middle of a reply that was once whole.

## Consequences

The first sound now arrives after the first sentence rather than after the
last. The sentence rule will occasionally be wrong - "Dr. Smith" at the end of
a long enough clause is a cut - and a wrong cut is a slightly odd pause, never
a lost word. The rule and the queue are plain objects over strings and
timestamps, testable without a provider or an audio element, which is where
the wrongness is expected to be found and narrowed.

Speech can now start while the client is still in the writing phase, so the
phase model allows speaking from thinking as well as from idle. The stored
recording is written once per reply, as before, and the provider is asked to
speak each sentence exactly once.
