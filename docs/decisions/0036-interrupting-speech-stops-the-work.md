# ADR 0036: Interrupting speech stops the work, not just the sound

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

Stopping playback moved a token forward, paused the audio element, and returned.
If synthesis was still in flight, the browser discarded the result when it
arrived. Everything downstream of that discard carried on regardless: the
provider finished generating the audio, the server read the whole body, wrote a
file, inserted a row, and rotated the audio cache to make room for it.

So "stop" meant "mute". On a deployment where the same machine is generating
pictures on a GPU, work nobody asked for is not free, and neither is a cache
that evicts audio somebody does want to make room for audio nobody will hear.

This is the first of the three provider-neutral voice items that can be built
against the existing Kokoro path, ahead of the listening evaluation that gates
provider selection.

## Decision

An interruption travels the whole way down.

**The browser aborts the request.** Playback holds the `AbortController` for the
synthesis it started, and `stop()` aborts it. An abort is the expected way a
synthesis ends when somebody interrupts, so it resolves quietly rather than
surfacing as a playback error - the person did this on purpose.

**The server watches for that abort.** Synthesis runs on a worker thread while
the request handler polls whether the browser is still connected. When it is
not, a cancellation token is tripped.

**The provider response is read in pieces.** Reading a whole body in one call
means an interruption is only noticed after the provider has finished, which is
the behaviour this replaces. Both speech clients now read in bounded chunks and
check the token between them. Closing the connection is what tells the provider
to stop generating.

**A cancelled synthesis writes nothing.** No artifact, no row, no rotation. The
audio cache is not disturbed for audio nobody will hear.

The same chunked read bounds how much a provider may return. A body larger than
the ceiling is refused rather than accumulated, because reading an unbounded
response into memory is a denial of service against this process wearing the
costume of a feature.

## Alternatives considered

**Leave it as it was and treat the waste as small.** Kokoro on the deployment
GPU is not small when it is competing with image generation, and the honest
description of the old behaviour - "interrupting mutes the output and leaves the
provider running" - is not something to keep.

**Cancel through a job, the way media generation does.** Synthesis is a single
short provider call on the reply path, not a queued unit of work. Giving it a
job id, a queue lane, and a cancel endpoint would be more machinery than the
thing being cancelled.

**Poll for disconnection from inside the synthesis call.** The provider call is
synchronous and blocking; it cannot watch a socket at the same time. Running it
on a worker thread while the request handler watches is what makes both possible
at once.

## Consequences

Stopping speech now means what the word means. The browser stops waiting, the
server stops reading, the provider stops generating, and nothing is written.

This is also the shape streaming speech needs. When synthesis becomes a
streaming response, the browser aborting the stream is what ends the provider
work - the same mechanism, doing more of the job. Building it this way first
means streaming does not have to invent cancellation as well.

What this does not do: it does not detect that somebody has started talking.
Turn detection is a separate item, and until it lands an interruption is
something a person does deliberately rather than something the product notices.
