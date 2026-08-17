# ADR 0037: Speech starts when the first audio exists, not when the last one does

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

Synthesis had to complete before playback could begin. A reply was generated in
full, written to disk, and served as a file; only then did the audio element get
a URL. The silence before a persona spoke was therefore the whole synthesis
time, every time, and it grew with the length of the reply.

That is the wrong shape for where this product is going. Latency per turn is the
thing to remove, not something to spend on tidiness.

This is the second of the three provider-neutral voice items that can be built
against the existing Kokoro path, ahead of the listening evaluation that gates
provider selection. It rests on ADR 0036, which made an interruption travel all
the way to the provider.

## Decision

Audio is delivered as it is produced, and playback starts on the first piece.

**The provider is asked to stream.** Kokoro's speech endpoint takes a `stream`
flag; both speech clients now expose a generator that yields pieces as they
arrive. `read` waits for a full buffer, `read1` returns what has arrived - that
difference is the whole of progressive delivery.

**The recording is still written.** The id it will be stored under is decided
before the first byte and sent in a response header, so the browser can register
the finished recording for replay while it is still listening to it. Replay is
unchanged: it plays the stored file rather than asking the provider to speak the
same words again.

**A stream that is abandoned stores nothing.** The artifact is written on the
line after the last piece is produced. A browser that walks away means that line
is never reached, so there is no file and no cache rotation to make room for one.

**Only formats that can start early are streamed.** WAV carries its length in a
header nothing can fill in halfway through, so a WAV reply cannot begin before
it is complete no matter how it is delivered. The browser asks whether it can
play the configured format incrementally, and when it cannot - a format with no
incremental form, or a browser without Media Source Extensions - it uses the
completed-file path exactly as before. The streaming endpoint refuses such a
format by name rather than pretending.

**A failure before any sound falls back; a failure after it does not.** If the
stream cannot be started, the completed file is fetched instead, which is a
recovery. If it fails partway, the completed file is not fetched, because
playing it would say the beginning of the reply a second time.

## Alternatives considered

**Point the audio element at a streaming URL.** The browser's own media stack
handles progressive playback natively, which would have been far less code. It
needs a GET, and the text to speak would have to travel in the URL. Reply text
is user content; it does not belong in a query string, in a log, or in browser
history.

**Chunk the reply into sentences and synthesize each as a file.** This gets the
first sentence out early without any streaming machinery, and it was tempting.
It also makes prosody worse at every seam, multiplies provider requests, and
leaves the product deciding where sentences end - a job it would get wrong on
exactly the replies people care about.

**Ask whether the connection is still open, rather than relying on
cancellation.** A request whose body has already been read has no pending
message to inspect, so the answer is a guess. The first version of ADR 0036 did
poll, and it reported every synthesis as abandoned. Cancellation of the request
task is the signal that means the same thing everywhere.

## Consequences

The wait before a persona speaks is now the wait for the first piece of audio,
not for all of it. On a long reply that is most of the delay removed.

The response body of the streaming route is not asserted through the test
client. Starlette streams directly only on ASGI spec 2.4 and above; below that
it races the body against a disconnect listener, and the test client queues a
disconnect as soon as the request body has been read, so the race is always
lost. The pieces either side of that seam are tested directly - the provider
generator, the service generator that stores only on completion, and the async
wrapper that hands pieces over and trips cancellation when it is closed.

What this does not do: it does not detect that somebody has started talking, and
it does not choose a provider. Turn detection and the listening evaluation
remain separate open items.
