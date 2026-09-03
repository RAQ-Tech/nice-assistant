# ADR 0038: Deciding when somebody has finished talking

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

The microphone was strictly hold-to-talk: pointer down starts, pointer up stops.
That is a good default and it has one property nothing else can match - the
release *is* the decision, and it is never wrong.

It is also the thing that stops this being hands-free. You cannot hold a button
while cooking, and a voice-first assistant that requires a hand on the screen is
not voice-first. The open item asked for automatic end-of-turn detection "with
push-to-talk retained as a dependable fallback rather than replaced", and the
second half of that sentence is the important half.

## Decision

Hands-free listening is an explicit setting, off by default, and holding the
button is unchanged.

With it on, the microphone button becomes tap-to-start and tap-to-stop, and the
product decides when the turn ended. With it off, nothing about recording
changes and no microphone level is ever measured.

The decision itself works to three deliberately conservative rules.

**Silence that was never preceded by speech never ends a turn.** Somebody who
taps and then pauses to gather a thought has not finished; ending there would be
the product deciding they had spoken when they had not. It keeps listening.

**The level that counts as speech sits above the level that counts as silence.**
A single threshold would flap for any voice sitting near it, cutting sentences
in half. The gap between the two is what makes the decision stable. A level in
between starts no silence timer and cancels none - a voice trailing off passes
through that band on its way down, and should still end the turn.

**There is a ceiling.** A room whose noise floor never drops below the silence
threshold would hold the microphone open indefinitely. After a minute it stops
and says why, pointing at the button that always works. A stop nobody asked for
is better than a microphone that never closes.

The decision logic is a plain object over levels and timestamps, with the
WebAudio wiring behind a separate seam. It is the part that will be wrong for
somebody, in a room this has never been in, so it is the part that has to be
testable without a microphone.

## Alternatives considered

**Replace hold-to-talk.** No. It is the only interaction here that cannot
misjudge, and the failure mode of the alternative - being cut off mid sentence -
is exactly the kind that makes people stop trusting a voice interface.

**On by default.** Turning a microphone from press-and-hold into
tap-and-it-listens is a change to how long the microphone is open. That should
be somebody's decision rather than a surprise.

**Send partial transcripts and let the transcription decide.** More accurate in
principle, and it needs streaming transcription, which needs a provider decision
this deployment has not made. Levels are available today and cost nothing.

**A single silence threshold.** Simpler, and it flaps. The hysteresis is two
constants and it is the difference between a usable feature and one that cuts
people off.

## Consequences

Hands-free listening exists and can be turned on. Hold-to-talk is untouched, and
when hands-free is off no level is measured and no audio context is opened.

The thresholds are defaults chosen from what room tone and speech usually
measure, not from this deployment. They are constructor options precisely
because the first real room may disagree, and tuning them should not mean
editing logic.

What this does not do: it does not transcribe while somebody is speaking, and it
does not know what was said before deciding they stopped. It is listening to
loudness, not to language.

## Amendment, 2026-09-03: the sending pause is a choice

The owner's natural pauses were "slightly too long" for 900 ms, and the
turn was sent before he had finished. How long somebody pauses to think is
not the product's to know, so the silence that sends is now a setting on the
Transcription page - quick (0.9 s, the default), relaxed (1.5 s) or patient
(2.5 s), bounded between 0.3 s and 5 s - read when hands-free listening
starts. The rules above are unchanged; only the length of the silence is.

The pause that cuts a recording for early transcription (ADR 0041) stays at
450 ms and stays separate. And when a turn ends after such a cut with nothing
said since, the silence that ended it is not sent to be transcribed: it
would cost the fixed transcription time and return nothing, which is how the
wait after the last word would otherwise grow by the whole pause.
