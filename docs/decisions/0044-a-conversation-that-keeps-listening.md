# ADR 0044: A conversation that keeps listening

- Status: accepted
- Date: 2026-10-27
- Owners: Nice Assistant maintainers

## Context

Hands-free listening (ADR 0038) decided when a turn ended, but only one turn.
After every reply somebody had to reach for the button again, so a "hands-free"
conversation needed a hand every turn. The 2026-10-27 project review found that,
and found the voice path fragile on the phone, which is where a voice companion
is most often held:

- Nothing reopened the microphone after a reply.
- The talk button said "Listening 0s" and never counted, and a second line above
  the composer repeated it, also frozen. Words transcribed at pauses (ADR 0041)
  were stored and never shown.
- The end of a turn was judged by fixed levels. In a room whose own noise sat
  above the silence level - a fan, a television - silence was never heard and a
  turn ran to the one-minute ceiling.
- A tap and a reopening could both open the microphone, and a held button let go
  before the microphone opened left it recording with nothing to stop it.
- New installations spoke in WAV, which cannot start before it is finished, so
  ADRs 0037 and 0042 quietly did nothing for anybody who never changed it.
- An iPhone has no `MediaSource`, only `ManagedMediaSource`, so it always waited
  for the whole reply.
- A phone plays sound only from a tap. The reply's audio element was routed
  through a Web Audio context created after a network wait, which a phone starts
  suspended, and the element was never played during a tap, so the first reply
  could be silent or blocked.
- A reasoning model's half-written `<think>` block could be read aloud with only
  its tag removed, and an aside like *smiles* was spoken as a word.

## Decision

**A tap starts a conversation, not a turn.** With hands-free listening on, once
the persona has answered and finished speaking, the microphone opens again by
itself and the next thing said is the next turn. The conversation ends when
somebody taps before saying anything, types instead, presses Cancel or Stop
audio, leaves the chat, a turn fails, or nobody answers for thirty seconds -
which ends it quietly: nothing is sent and nothing is reported as wrong. A
recording with no words in it gets one more chance, so a cough does not end the
conversation and a noisy room cannot keep it transcribing forever.

**Half-duplex.** The microphone is closed while the persona speaks and opens
300 ms after it finishes. Listening while it spoke would hear it, and on a phone
an open microphone moves the persona's voice to the earpiece. Talking over the
persona remains a tap, which stops it and listens. Noticing unaided that
somebody has started talking over it is later work and is not claimed here.

**Each turn listens to the room first.** The first 400 ms set the levels: the
quietest moment is taken as the room, and speech and silence sit 2.5 and 1.6
times above it, never below the old fixed levels and never above levels ordinary
speech reaches. A quiet room is judged exactly as before. What was said in those
first moments is replayed into the decision, so a quick "yes" still counts.
Echo cancellation, noise suppression and gain control are asked for by name.

**One place says what is happening.** The talk button reads Listening,
Transcribing or Thinking, or tells a held button to be let go, and while it
listens hands-free a ring around it moves with the voice it hears. The frozen
counter and the line repeating it are gone. Words transcribed at pauses appear
in the message box, the way dictation does, until they are sent.

**Speech plays on a phone.** The page keeps one Web Audio context, created and
resumed on taps, which the microphone meter and the visualizer borrow instead of
making their own. The first tap anywhere plays a twentieth of a second of
silence through the reply's audio element. The visualizer routes that element
through Web Audio only while it is on, and draws only then. An iPhone streams
through `ManagedMediaSource` with AirPlay turned off for the element, which that
source requires; a browser that offers neither falls back to the finished file
as before.

**MP3 is the default speech format.** One constant on each side, with a test
that fails when they disagree. Migration `0042` moves accounts on WAV - the old
default, stored for everybody because the browser saves every setting - to MP3,
and leaves every other format alone, following migration `0039`.

**Asides and reasoning are not spoken.** `<think>` blocks are left out whether
or not they close. An aside between single asterisks at the start of a line or
just after a sentence ends is left out; inside a sentence it is emphasis and is
said. While a reply is still arriving, an opened aside or reasoning block holds
back what follows it, and the speakable text only ever grows, which is what
speaking a sentence at a time (ADR 0042) depends on.

## Alternatives considered

- **Keep the microphone open across turns.** Saves the moment it takes to reopen
  it, at the cost of hearing the persona and, on a phone, sending its voice to
  the earpiece. Rejected until there is echo handling that can be trusted.
- **Adapt the levels continuously.** A level that drifts with what it hears can
  drift up under a long sentence until speech reads as silence and the turn is
  cut mid-word. Measuring once per turn, before anyone speaks, cannot.
- **Keep counting seconds on the button.** A number that changes ten times a
  second means rebuilding the page ten times a second; the ring moves without a
  render and says the more useful thing - that it hears you.
- **Leave stored WAV alone.** Nobody here would choose WAV for anything it does
  better, and leaving it means every account that never touched the setting
  keeps waiting for whole replies.

## Consequences

Hands-free listening means a conversation now, and its first tap is the only one
needed. A conversation the person forgets about ends itself after thirty seconds
of nobody answering. Nothing about held recording changed except its label and
the fix for a release that came too early. Accounts on WAV now speak MP3. The
visualizer, when on, still routes through Web Audio and is silent whenever the
system has paused audio until the next tap.

## Verification

- `frontend/tests/conversation_loop.test.ts`: listens again after a spoken and
  an unspoken reply, not while the persona speaks; ends on silence, a failed
  turn, or a second wordless recording; a tap that already started listening
  wins; the button's taps and labels.
- `frontend/tests/turn_detection.test.ts`: a noisy room ends a turn, speech in
  the first moments counts, a reopened microphone nobody answers stops quietly,
  the microphone opens once however quickly it is asked twice, echo cancellation
  is requested, an early release is honoured.
- `frontend/tests/phone_playback.test.ts` and `speech_text.test.ts`: the silent
  unlock, the visualizer leaving the element alone when off, the managed source,
  and what is and is not spoken, including that speakable text never shrinks.
- `tests/test_speech_format_default.py`: the default is streamable, the browser
  agrees, no second default is written down, and the migration moves only WAV.
- In Chromium with a scripted microphone and mocked routes, a tap, a sentence
  and a pause produced a sent turn, a reply, and the microphone listening again;
  a tap before speaking ended it. Unaccepted on a real phone and on the
  installed deployment until the owner tries it.
