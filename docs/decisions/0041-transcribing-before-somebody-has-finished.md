# ADR 0041: Transcribing before somebody has finished talking

- Status: accepted
- Date: 2026-08-18
- Owners: Nice Assistant maintainers

## Context

Every other part of a spoken turn had been made to overlap with itself. Speech
starts on the first piece of audio rather than the last (ADR 0037), interrupting
it stops the provider work (ADR 0036), and the product decides when a turn ended
rather than waiting for a button (ADR 0038). Transcription was the last piece
that still happened strictly afterwards: the whole recording was uploaded when
the turn ended, and everything waited on it.

That wait is not small, and it is not proportional to how long somebody spoke.
Measured against the deployment this was built for, transcription costs about
6.5 seconds of fixed work plus half a second per second of audio - because
Whisper's encoder always runs on a padded thirty-second window no matter how
briefly anybody talked. A four-second sentence and a fifteen-second one differ
by five seconds of work and nothing else.

## Decision

**Cut the recording at its pauses, and transcribe each piece while the next one
is still being spoken.** When the turn ends, only the final piece is outstanding,
so the wait is one sentence rather than the whole answer.

**A pause is not an ending, and the two decisions stay separate.**
`EndOfTurnDetector` is unchanged. It has to be conservative, because deciding
wrongly that somebody has finished cuts them off mid-thought. `PauseDetector` is
a different question with a different cost: deciding wrongly where to start work
early wastes a little effort and nothing else. It fires on a shorter silence -
450ms against 900ms - and once per pause rather than once per quiet sample.

**Each piece is a whole recording, not a slice of a stream.** The recorder is
stopped and restarted at each cut, so every piece is a file a decoder can open
on its own. A webm stream sliced at arbitrary byte offsets is not. The few
milliseconds lost at the boundary fall inside the pause that caused the cut.

**No server change.** Each piece is an ordinary transcription request. The
browser reassembles them, which means this works identically against Wyoming, an
OpenAI-compatible service, or OpenAI itself, and none of them had to learn
anything.

**Pieces claim their place before the work starts.** They come back in whatever
order the service finishes them, which is not the order somebody said them.

**A piece that fails leaves a hole rather than failing the turn.** Most of what
somebody said is still worth having, and an error over a sentence that mostly
arrived is worse than the gap.

**Off by default, and the setting says why.** This transcribes more audio in
total, not less - each piece pays that fixed encoder cost again. On a fast model
that is a clear win, because the pieces are transcribed during time that was
being spent listening anyway. On a large model on a busy machine it is a clear
loss: three pieces at 6.5 seconds each is more total work than one at eight, and
they contend with each other. The product cannot know which case a deployment is
in, so it does not guess.

## Alternatives considered

**Real streaming transcription, with partial results refined as more audio
arrives.** What "transcribing while somebody speaks" sounds like it should mean,
and not available here. `wyoming-faster-whisper` transcribes on `audio-stop`; it
buffers rather than decoding incrementally, and the OpenAI transcription API is
request-and-response. Building this would mean running a different class of model
entirely.

**Cut on a fixed timer rather than at pauses.** Simpler, and it cuts words in
half. Whisper is good at continuous speech and bad at fragments that begin
mid-syllable, so the seams would show up as errors in the transcript.

**Capture raw PCM through an audio worklet and encode each piece as WAV.** Avoids
restarting the recorder and loses no audio at the boundary. It also means writing
an encoder and carrying it, to recover milliseconds of silence.

**On by default.** Rejected on the measurement above. A default that makes a
spoken turn slower on the hardware this was built for is not a default.

## Consequences

The wait after somebody stops talking now depends on how long their last
sentence was rather than how long they spoke for. What was said earlier appears
as it is transcribed, so a long answer visibly fills in rather than arriving
whole after a silence.

The setting is where the honesty lives. It states that this does more total work
and suits a fast model, because somebody switching it on with a large model on a
contended GPU would otherwise experience a performance feature as a regression
and have no way to know why.

Nothing about the held-button path changes, and nothing about end-of-turn
detection changes. With the setting off, the recording path is exactly what it
was.
