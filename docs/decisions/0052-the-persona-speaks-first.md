# ADR 0052: The persona speaks first

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

The 2026-10-27 review's first-run batch (ADR 0047) promised two things it did
not deliver: choosing the companion's voice while meeting them, and a companion
that says hello first. Setup ended in an empty conversation reading "Say hello
to Nova.", which is a form waiting to be filled in rather than somebody glad you
came. A voice could only be chosen afterwards, on the persona's page, once a
picker that plays samples existed (ADR 0051).

A reply needs something in front of it. Every turn was a user message and its
answer, and the turns table required the message. The obvious shortcuts were
each wrong in a way that would last: a hidden user message saying "greet me"
would be replayed by every later prompt as something the owner said, and would
be counted, summarised and mined for memories as theirs.

## Decision

**A turn the persona opens.** `POST /api/v1/chats/{chat}/greeting` makes a turn
with no user message. It is otherwise an ordinary turn: queued, streamed
through `/api/v1/turns/{turn}/events`, filtered, kept as the persona's message,
and spoken like any reply. Its prompt is the persona's own instructions, the
owner profile and whatever else an ordinary prompt carries, followed - where the
message being answered would be - by one instruction from the application, as
a system message: nothing has been said yet, greet them in your own words, a
sentence or two. That instruction exists only in that one prompt. It is never
stored, so a later prompt holds the greeting as something the persona said and
nothing else.

Migration `0045` makes `conversation_turns.user_message_id` optional. SQLite
rebuilds the table to do it, and dropping a table that memories, jobs, picture
requests and task-model runs point at would set their links to nothing, so each
link is kept and put back.

**Only an empty conversation can be opened.** Anything already said, or a turn
still being answered, refuses with `409`. A greeting names nothing - the chat is
still titled by the first thing the owner says - and plans no picture and
extracts no memory, since it holds nothing the owner said. "Answer again" on a
greeting with nothing said since greets again; deleting it leaves the empty
conversation.

**Setup ends in the greeting.** "Start talking" opens the new conversation and,
once it has loaded, asks for the greeting. If it cannot be had - the model is
unreachable, or fails partway - nothing is shown: no error, no half reply, just
the quiet "Say hello to Nova." that was there before.

**The voice is chosen on the setup page.** When replies are spoken by the local
speech service, setup shows the same picker as the persona page: the voices the
service has, named and grouped, a sample of each, and first "Same as Spoken
replies". A chosen voice is stored on the new persona under `local`, so it is
the voice of the greeting and every reply after it. With speech off nothing is
shown. Trying setup again after a later step failed still makes one persona,
and now also keeps a voice, name or description changed in between.

**No status in the conversation.** While a reply was on its way, its bubble said
"Queued" or "Thinking…". It shows three dots now, until the first words, and
still stops the reply when pressed. What the server is doing stays where ADR
0044 put it, on the talk button.

## Alternatives considered

- **A hidden user message asking for a greeting.** No schema change, but every
  later prompt would replay it as the owner's words, and the summary, memory
  extraction and "Nothing said yet" would all have to learn to skip it.
- **A canned greeting written by the application.** Instant and never fails,
  but it is the application speaking in the persona's place, in nobody's voice.
- **A separate, unstored greeting stream.** Would need its own streaming, speech
  and replay path, and the greeting would vanish on reload.

## Consequences

Opening a first conversation costs one model reply, after setup and before the
owner speaks; no turn the owner sends waits for it or carries more prompt
because of it. A failed greeting leaves a failed turn with no messages, which
nothing shows. A speech service that was not on before setup cannot be chosen
from during it: a new account starts with spoken replies off, so on a fresh
install the voice is chosen later, on the persona's page, unless speech was
turned on first.

## Verification

- `tests/test_persona_greeting.py`: the greeting turn has no user message, its
  prompt ends with the application's instruction and holds no user message; it
  is kept as the persona's message, names nothing, plans nothing, learns
  nothing; the next prompt holds it as said and never the instruction; a
  conversation with anything in it refuses; a failed greeting leaves it empty;
  a greeting can be said again and deleted.
- `tests/test_database_foundation.py`: migration `0045` keeps every link to an
  existing turn, allows a turn with no user message, and keeps the status check,
  the per-chat sequence and the indexes.
- `frontend/tests/greeting.test.ts`: the conversation setup opens is greeted
  once, with no message from the owner, streamed and spoken; a greeting that
  cannot be had or fails partway shows nothing and keeps a half-typed message;
  "Answer again" greets again.
- `frontend/tests/first_run.test.ts`: the setup voice is offered from the
  service's voices with a sample, hidden when speech is off, stored under the
  provider, empty when the usual voice is kept, and kept across a retry without
  a twin.
- `frontend/e2e/app.spec.ts`: a first visit chooses a voice by ear and is greeted
  first, spoken by the persona with nothing technical in the conversation; with
  speech off and the model unreachable it ends in the quiet invitation.
- Not verified here: the greeting from a real model, spoken by the real speech
  service on the deployment.
