# ADR 0047: Signing in, staying signed in, and the first run

- Status: accepted
- Date: 2026-10-28
- Owners: Nice Assistant maintainers

## Context

Batch 4 of the 2026-10-27 project review: the first five minutes. A first visit
opened on "Your session ended. Please sign in again." although there had never
been a session. The sign-in screen was two loose inputs and two buttons, so a
password manager neither offered to save the password nor filled it, Enter did
nothing, and creating an account cleared the form and asked for the same words
again. Every sign-in ended after thirty idle minutes, so a phone on the kitchen
counter asked for a password most times it was picked up. First-run setup was
three dialogs that asked for a "workspace" name and a persona "instruction"
defaulting to "Be helpful and concise", and cancelling any of them left a chat
screen with no persona and a Send button that did nothing. Reloading during
setup skipped it altogether, to the same dead end.

## Decision

**A real sign-in form.** A `<form>` with labelled `username` and `password`
fields carrying the `username`, `current-password` and `new-password`
autocomplete values, submitted by Enter. What was typed stays in place when
sign-in fails, and the form goes only once sign-in has worked, which is the
signal a browser reads as success. Creating an account is offered only where the
server would allow it - the first account, or any account when
`ALLOW_PUBLIC_SIGNUP` is on - which an unauthenticated `GET /api/v1/signup` reports. A new account signs
straight in. Passwords keep the eight-character minimum; nothing is seeded or
generated.

**Staying signed in on a kept device.** "Keep me signed in on this device" is on
by default. Such a session lasts sixty days from its last use, renewed by every
request, and is never ended for being idle, in the server or the browser; its
cookie outlives closing the browser. Unticked, a session behaves as before: the
idle limit applies when automatic sign-out is on. Signing out ends either kind at
once. The choice is per sign-in, stored on the session, so one shared tablet can
be signed in briefly while a phone stays signed in.

**"Your session ended" only when one did.** The message appears when a session
that existed is refused, never on a first visit or a failed sign-in, which says
"Wrong username or password."

**One setup page, and nothing else until it is done.** Signed in with no persona,
the app shows only "Meet your companion": their name, what they are like
(prefilled with a warm, conversational description rather than an assistant's),
and the model they think with, preferring one that can see photos. If Ollama
lists no models the page says so, says what to check, and can look again. "Start
talking" makes the workspace quietly, the persona, the default model, and a
first conversation, and opens it with "Say hello to Nova." A step that fails
leaves the page and everything typed, and trying again does not make a second
persona. An install that already has a persona is marked set up without asking.

**Small honesty fixes on the home page.** A conversation nobody has spoken in
says "Nothing said yet" rather than "Last spoken" with its creation time.

## Alternatives considered

- **Lengthen the idle limit for everybody.** Simpler, but it takes the idle
  sign-out away from the shared screen that wants it, and a long fixed limit
  still asks for a password on a phone used weekly.
- **Remember the password in the browser's own storage.** Never: credentials stay
  out of client storage, and a password manager does this better once the form is
  one it recognises.
- **Keep the setup dialogs but forbid cancelling them.** It removes the dead end
  but keeps the questions about the product's internals, and a reload still
  bypassed dialogs that only ran after an explicit sign-in.

## Consequences

A kept device is signed in for as long as it is used, so losing an unlocked phone
means deleting its row from the database's `sessions` table until a list of
signed-in devices exists. The setup page needs a reachable Ollama
to suggest a model; without one, setup can still finish and a model is chosen
later. `onboarding_done` is still stored, but whether setup is shown follows
whether a persona exists, which cannot drift from what the app can do.

## Verification

- `tests/test_sessions.py`: the first visit reports that the first account can be
  made, and afterwards that none can when public sign-up is off; a kept session
  outlives the idle limit with a sixty-day cookie; an unkept one still ends.
- `frontend/tests/first_run.test.ts`: the sign-in form's names, labels and
  autocomplete values; "keep me signed in" is sent; a wrong password keeps what was
  typed; the first account signs straight in; setup prefers a model that can see,
  ends in the new conversation, does not make a twin on retry, and can look for
  Ollama again.
- `frontend/e2e/app.spec.ts`: a first visit shows no false "session ended", creates
  the account with Enter, cannot reach the chat before setup, and lands in a
  conversation that invites the first word; a wrong password says so and keeps
  what was typed.
- Not verified here: that Chrome on the owner's profile offers to save and fill
  the password, which only a real browser profile can show. Item 20 in BACKLOG
  section 4.
