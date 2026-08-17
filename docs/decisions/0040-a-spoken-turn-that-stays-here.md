# ADR 0040: A spoken turn can finish without leaving this network

- Status: accepted
- Date: 2026-08-17
- Owners: Nice Assistant maintainers

## Context

Chat, memory, images and the voice you hear could all run on the machine in the
room. The voice you *speak* could not. `SpeechService.transcribe` refused every
provider but OpenAI with a 501, so holding the microphone button either sent
audio to a cloud service or did nothing at all.

That was the single place the product contradicted what the owner has said it is
for: an explicit conversation, fully contained on his own hardware. It is a
problem twice - the privacy one, and the fact that the only working provider has
a usage policy that the conversations this is built for do not satisfy.

The settings schema already accepted `local`. Somebody could select it, save it,
and then discover at the microphone that it had never been implemented. An
option that can only fail is worse than no option.

## Decision

**A Whisper service on the network, reached over HTTP, in the shape OpenAI
documents.** No Python dependency, no model weights in the container, no GPU code
in the application process.

This is not a new idea here - it is exactly what the local speech path already
does. Kokoro is a separate service at a configured address, and the product
speaks its API rather than embedding it. Transcription now works the same way,
which means the deployment story, the URL policy, the connection check and the
failure copy are all the ones that already exist.

**The OpenAI shape rather than one server's own.** `POST /v1/audio/transcriptions`
with a multipart body is what speaches, whisper.cpp's bundled server, and LocalAI
all implement. Speaking the shape keeps this a configuration choice. Binding to
one of them would make it a dependency, and would pick a winner among projects
that are still moving.

**No credential is sent to a local service.** A service on the private LAN that
demanded an API key would be a different kind of thing than this is for, and
sending one would leak it to whatever else is on that network.

**The address is held to the private-LAN policy at save time.** "Local" has to
mean local. Without this, pointing the setting at a host on the internet would be
a quiet way to send every recording off the machine under a label that says it
does not.

**A plain-text answer is accepted as a transcript.** whisper.cpp's server replies
`text/plain` unless asked otherwise. Refusing a correct transcript over its
content type would be pedantry that a person experiences as a broken microphone.

**The model is a setting, not a constant.** `whisper-1` is the default because
most self-hosted servers accept OpenAI's name as an alias for whatever they
loaded. The ones that want their own identifier say so plainly, and this is a
text field, so they can have it.

## Alternatives considered

**`faster-whisper` or `openai-whisper` inside the application process.** The
obvious reading of "local transcription", and refused. It puts model weights and
CUDA in a container that has neither today, it competes for the same 12 GB card
as the chat model and image generation, and it makes the transcription quality a
release decision rather than the operator's. The service boundary keeps a bad
model swap from being a redeploy.

**Ollama, which is already running.** It does not do speech-to-text. Worth
stating because it is the first thing anybody would reasonably ask.

**Shipping a Whisper container in the compose file.** Tempting, and it is what
makes this easy rather than merely possible. Held back deliberately: the same
question applies to Kokoro and ComfyUI, both of which are separately deployed,
and answering it for one service and not the others would be the inconsistent
half of a decision worth making properly.

**Leaving the 501 and documenting it.** What was there. It is honest, and it
leaves the product's stated purpose unmet.

## Consequences

An explicit conversation can now be held with nothing leaving the machine: the
words in, the words out, the reply, the memory of it, and the pictures. The
homepage locality line reports this without being told to, because it reads the
provider rather than a hard-coded list, and the note beside the microphone that
said where a recording was going now shows nothing - it was written to disappear
on its own when this landed, and it did.

What this does not do is run a Whisper service. Somebody still has to deploy one,
the same way they deployed Kokoro. Until they do, the connection check says so
and the microphone reports a provider failure naming the local service rather
than a generic one, so it is possible to tell whose box is down.

Transcribing while somebody is still speaking remains unimplemented and is still
listed as such.
