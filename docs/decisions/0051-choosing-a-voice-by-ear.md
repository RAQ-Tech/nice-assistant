# ADR 0051: Choosing a voice by ear

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

A voice was a text box. Under Spoken replies, the local voice was set by typing
a Kokoro name such as `af_heart`, with nothing to say which names existed or
what any of them sounded like. The server could already ask Kokoro for its voice
list; nothing used it. The field's hint said "unless a persona chooses its own",
but no page let a persona choose one, although personas have stored per-provider
voice preferences since the provider-keyed rework.

## Decision

**A list of the voices the service actually has.** The voice is a dropdown of
what the local speech service reports. Each voice code is written out: `af_heart`
reads as "Heart · female" under American English. Voices are grouped by
language with English first. A voice set earlier that the service no longer
lists, or a blend such as `af_bella(2)+af_heart(1)`, stays chosen and visible
rather than being changed silently. When the service cannot be reached the box
to type a name returns, so a voice can still be set. The list is asked for once
per service address, and again only after the address has stopped changing.

**A sample beside it.** A play button speaks one fixed sentence in the chosen
voice at the chosen speed. The sentence is: "Hi, it's lovely to meet you. This
is how I'll sound when we talk." `GET /api/v1/speech/samples` has the service
make it the first time and keeps it under `DATA_DIR/voice-samples`, keyed by
everything that changes the sound: service, model, voice, speed and sentence.
Every later play is a recording. The folder is a cache: not backed up, safe to
delete, remade on demand. Only the local service makes samples; browsing cloud
voices would send requests off the machine just to listen.

**A persona can have its own voice.** The persona page has the same picker. Its
first choice is "Same as Spoken replies", naming that voice. The choice is
stored under the provider in use, so a local voice leaves an OpenAI one alone.

## Alternatives considered

- **Recordings shipped in the image.** Twenty-eight English voices of audio in
  every image, tied to one Kokoro version, and missing any voice added later.
  Making each sample on first use costs one synthesis per voice, once.
- **Playing through the reply path.** Every sample would be stored and rotated
  as a reply's audio, pushing real replies out of the replay window.

## Consequences

The first play of each voice waits for one synthesis, about a second on the
deployment. A persona's voice now applies wherever that persona speaks. OpenAI
voices remain a typed name with no sample.

## Verification

- `tests/test_voice_samples.py`: a sample is made once and then replayed; a new
  speed makes a new one; only a voice name is accepted; a cloud provider is not
  sampled; the cache is not in a backup.
- `frontend/tests/voice_picker.test.ts`: names, grouping, a kept unlisted voice,
  the typed fallback, the inherited choice, playing at the chosen speed, and a
  failure that says so.
- `frontend/tests/persona_settings.test.ts`: a persona voice is chosen, marks
  the page changed, and is saved under the provider in use.
