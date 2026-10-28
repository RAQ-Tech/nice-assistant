# ADR 0045: Taking back what was said

- Status: accepted
- Date: 2026-10-28
- Owners: Nice Assistant maintainers

## Context

The 2026-10-27 review compared the chat with what anybody expects of a
messaging app and found the basics missing. A reply that failed could not be
tried again; the only way out of a bad turn was a new chat. Nothing could be
answered again, corrected, or deleted. A message whose request never reached the
server was lost with the failure. A reply stream that dropped - a phone locking,
a Wi-Fi blip - stopped the live text and the speech, and one that closed cleanly
midway was reported as a failed turn while the server was still writing it.
Replies showed `**` and `*` as typed, joined separate lists, and loaded a picture
from whatever address a reply named. Every streamed word rebuilt the whole page,
and a picture being made re-fetched the whole conversation twice a second. On a
phone, six buttons sat under every message.

## Decision

**Answering again is one action, on the latest exchange.** `POST
/api/v1/chats/{chat}/messages/{message}/again` forgets the latest exchange and
sends its words again as a new turn, in one transaction: with no `text` it is a
retry of a failed reply or a new answer to the same words; with `text` it is a
correction, and only the corrected words are ever answered. Earlier exchanges are
refused, because every reply after one would be answering something no longer
said. A reply still being written, or one whose picture is still being made, is
refused until it finishes or is stopped. Follow-ups still owed to the forgotten
exchange - title, picture planning, memory candidates, summary - are cancelled.

**Deleting takes the whole exchange.** `DELETE
/api/v1/chats/{chat}/messages/{message}` removes a message together with the
reply it belongs with, since half an exchange is either a reply to nothing or a
question the next prompt answers twice. A message no turn claims goes on its
own. Every summary that reaches the deleted message is dropped too, so nothing
goes on remembering it; compaction writes a new one from what is left. Records
that point at the turn - task-model runs, capability requests, memories - keep
their rows with the turn unset, as the schema already provides.

**Nothing typed is lost.** A message whose request never reached the server goes
back into the box it was typed in.

**A dropped stream is picked up where it stopped.** The browser resumes the
reply stream from the last event it received, using the replay the server
already keeps (ADR 0034), up to three times. When a stream ends without saying
how the turn went, the browser asks; a turn that finished is finished, and only
after the retries does it fall back to waiting for the result. Returning to a
hidden page refreshes the conversation.

**Replies are formatted, and only this server's pictures load.** Bold, emphasis,
headings, separate bulleted and numbered lists, quotes and code are rendered from
text that has already been escaped, so a model still cannot write HTML. An aside
at the start of a line or sentence - *smiles* - is shown as an aside, by the same
rule that keeps it out of speech (ADR 0044). A picture loads only from a path on
this server; anything else becomes a link, so reading a reply never tells another
host it was read.

**Messages say when, quietly.** A small line shows when the conversation picked
up again - after half an hour or on a new day - the way a messaging app does,
and each message gives its exact time on hover.

**Buttons wait to be wanted.** On a screen with no hover, a message's buttons stay
hidden until it is tapped. The latest reply offers "Answer again", the latest
message "Edit", any stored message "Delete" after a confirmation, and an
unanswered latest message shows "Try again" in place, rather than in a banner.

**The page redraws as often as it can be seen.** Streamed words are drawn at most
once per frame, a finished message's formatting is worked out once, and a picture
being made re-fetches the conversation only when its own state changes.

## Alternatives considered

- **Keep alternative replies, with arrows between them.** The way some assistants
  do it, at the cost of a branching transcript that every prompt, summary and
  memory would have to choose a path through. Replacing keeps one conversation.
- **Answer any earlier message again.** Would leave every later reply answering
  something no longer said, or force them all to be thrown away silently.
- **Delete a single message.** Leaves a reply to nothing or a question answered
  twice, and the next prompt reads both.
- **A markdown library.** Larger than everything here that needs formatting, and
  its own idea of what HTML to let through. The escape-first renderer is a page
  of code with one rule about markup.

## Consequences

A conversation can be repaired from inside it. Answering again, editing and
deleting are permanent: the old words are gone, and the summary forgets them too.
A deleted exchange that a summary had covered costs one compaction later. Sending
a photo into the conversation is not part of this: what the persona would do with
it is a product choice that needs a model able to see, and it is listed in the
backlog as waiting on that decision.

## Verification

- `tests/test_message_actions.py`: a failed reply tried again is answered once;
  a reply answered again replaces the old one; a correction is the only text the
  model sees; only the latest exchange can be answered again; nothing while a
  reply is still written; owed memory extraction is cancelled; deleting removes
  the exchange from the transcript and the next prompt, drops a summary that
  covered it, and cannot reach another person's chat.
- `frontend/tests/chat_actions.test.ts` and `markdown.test.ts`: again and edit
  send the right request, a request that never arrived restores the draft, a
  dropped stream resumes from its last event, the list offers each action only
  where it applies, time dividers appear only after a gap, formatting renders,
  and a picture from elsewhere is not loaded.
- Checked in Chromium at desktop and phone sizes with mocked routes: a failed
  message tried again, an edit sent, formatting, dividers, and tap-to-show
  buttons on the phone layout.
