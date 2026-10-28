# ADR 0046: A photo the persona can see

- Status: accepted
- Date: 2026-10-28
- Owners: Nice Assistant maintainers

## Context

Sending a photo into a conversation was the one part of the messaging basics
(ADR 0045) left waiting on a product choice: what the persona should do with
it. The owner chose on 2026-10-28 that the persona sees it - "look at my dog"
is answered by somebody who has looked - using a chat model that can see
images. Ollama serves several (Gemma 3, LLaVA-family and others) and takes
images beside a message's text.

## Decision

**The persona sees a photo sent with a message.** The browser shrinks it - a
phone photo is several megabytes; a model looks at about a megapixel and a half -
and sends it with the message. The server decodes it, turns it upright, strips
everything but its pixels, including where and when it was taken, and re-encodes
it at no more than 1568 pixels on its longest side. A photo may be sent with no
words at all.

**Only to a model that can see it.** Ollama says which models take images:
recent versions list a `vision` capability, older ones show the vision tower in
the model's details, and both are read. The camera button appears only when the
conversation's model can see, and the server refuses a photo for one that
cannot, before writing anything, naming the model and what to choose instead. A
reply about a photo the model never saw would be worse than no reply.

**Kept with the message, in the database.** A photo is part of what was said:
it is stored as its normalized bytes beside the message, goes when the message
or chat goes, is in every backup the message is in, and is served back only to
whoever sent it. Answering a message again or correcting it (ADR 0045) keeps its
photo.

**Seen while it is being talked about.** The current message's photo is always
shown to the model, and the most recent earlier one while it is among the last
three things said, so "what breed is she?" is answered by somebody who can still
see the dog. Older photos are not sent again: each costs the reply time. What a
photo costs in prompt is not reported before it is sent, so 600 tokens a photo
is set aside from what the text may use - more than Gemma 3's 256, less than
the worst case - and recorded in the turn's estimate.

**It stays on this network.** The photo goes to Ollama, which the product
already requires to be on the private LAN. No cloud service is involved and
nothing asks for consent, because nothing leaves.

## Alternatives considered

- **Use a photo only as a reference for picture editing.** Needs no new model,
  and is what ADR 0029's attachments already half do, but it is not what
  showing somebody a photo means.
- **Describe the photo with a separate model, and give the persona the text.**
  Works with any chat model, at the cost of a second model call in front of
  every photo reply and a persona that answers a description rather than the
  picture. Rejected for latency (ADRs 0043 and 0044 removed exactly this kind of
  cost) and because a description loses what somebody wanted noticed.
- **Keep photos on disk.** The media library does, for generated pictures. A
  photo somebody sent belongs to the message's lifecycle, not the library's, and
  on disk it would need its own deletion and backup paths.
- **Send every photo in the conversation every turn.** Each one is hundreds of
  prompt tokens and a slower reply for photos nobody is talking about.

## Consequences

The feature needs a chat model that can see. On the deployment that is a model
choice - Gemma 3 at 12B, quantised, fits the 12 GB card - and the camera button
appears once a persona uses one. Photos make the database larger, by a few
hundred kilobytes each. A persona answering on a model that cannot see never
offers to take a photo, so nothing claims an ability that is not there.

## Verification

- `tests/test_chat_photos.py`: the model receives the photo upright, no larger
  than 1568 pixels and without its metadata; a photo alone is a message; a model
  that cannot see refuses before anything is kept; a photo is served only to
  whoever sent it; a follow-up still sees it and a later turn does not; answering
  again keeps it and deleting the exchange takes it; anything but a picture is
  refused; both ways Ollama reports vision are read.
- `frontend/tests/photos.test.ts`: the photo travels with the message and comes
  back to the box when it never arrived; the camera button appears only where the
  photo would be seen; a chosen photo can be taken back; a sent photo is shown in
  its message.
- Checked in Chromium at desktop and phone sizes with mocked routes: attached,
  previewed, sent shrunk as a JPEG, and shown in the conversation.
- Deployment: with a vision model chosen for a persona, send a photo from a phone
  and get an answer about what is in it. That is BACKLOG item 19.
