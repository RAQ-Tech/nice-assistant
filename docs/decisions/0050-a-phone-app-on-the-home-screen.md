# ADR 0050: A phone app on the home screen

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

Batch 5 of the 2026-10-27 project review: the phone. The owner chose an
installable web app over native Android and iPhone apps - one codebase, no
store accounts, and updates that arrive with the container. What stood in the
way:

- Nothing could be put on a home screen.
- The screen dimmed and locked mid-conversation, which stops the microphone.
- A held Hold to Talk ended if the finger drifted: the browser turned the moving
  touch into a scroll and cancelled it, or it slid off the button's edge.
- The header and message buttons were text characters and emoji (☰ ⋯ ⚙ ↪ 🧠 🗑),
  which drew differently on every phone, ignored the theme, and were read aloud
  as "gear" and "brain".
- A screen reader heard neither replies nor errors. Every redraw sent keyboard
  focus back to the top of the page, and the chat list could not be reached
  from a keyboard at all.
- A light theme existed, but about a hundred colours were written into
  individual rules and ignored it.

## Decision

**Installable.** A web app manifest names the app and its icons, rendered from
the existing mark at 192 and 512 pixels, plus an edge-to-edge maskable icon and
an iPhone touch icon. A small service worker caches one thing: a page saying
the server cannot be reached. Every other request goes to the server, so an
installed app can never keep running an old build. Installing needs HTTPS,
which the owner's deployment already has.

**The screen stays on while talking** - listening, transcribing, waiting, or
speaking - and for one minute after, through the Screen Wake Lock API. It is
asked for again when the page returns from the background. Where a browser has
no wake lock, nothing else changes.

**Holding is decided by letting go.** The end of a hold is heard on the window,
not the button, so drifting off the edge or the button being redrawn under the
finger does not end it, and `touch-action: none` stops the browser treating
the hold as a scroll. A press shorter than 350 ms is a brush and is discarded,
not transcribed. A second finger is ignored, a long press opens no menu, and
Space or Enter holds it from a keyboard. A cancel while the microphone is still
opening now takes effect instead of being dropped.

**One icon set.** Line icons in `icons.ts`, drawn on a 24-unit grid in the text
colour and hidden from screen readers; each icon-only button carries a label.

**Heard, and reachable.**
- **Announcements:** two live regions sit outside the redrawn page and are
  written to only when something new happens. A reply is read once its turn has
  finished, never word by word and never as history when a chat opens. An
  error is read at once.
- **Focus:** focus survives redraws for any control, matched by its name rather
  than its position.
- **Dialogs and keys:** a dialog that opens takes focus, and Escape closes the
  chat list.
- **The chat list:** each chat is a real button, with the current one marked.

**Themes are tokens.** Every colour lives in the two theme blocks at the top of
the stylesheet; rules name tokens. Things laid over a picture or video keep
their own tokens that do not change with the theme. "Match my device", the new
default, follows the system setting live; Light and Dark stay available. The
last choice is remembered in the browser so the first paint is already right.

## Alternatives considered

- **Native apps.** Background listening, locked-screen conversation and iPhone
  notifications are what they would add; two store presences and three
  codebases are what they would cost. Revisit if hands-free use with the phone
  in a pocket becomes the main way the app is used.
- **A caching service worker.** It would let the app open with no connection,
  but nothing works without the server, and a cached bundle is exactly the
  stale-build fault the server's `no-cache` headers were added to stop.
- **A second light block inside a `prefers-color-scheme` query.** Two copies of
  every light token drift apart; resolving "match my device" in `theme.ts`
  keeps one.

## Consequences

The new default theme means an install whose theme was never saved now follows
the device; one that saved Dark stays dark. A rule that writes a colour literal
fails the build. A press shorter than 350 ms on Hold to Talk no longer sends
anything.

## Verification

- `tests/test_browser_delivery.py`: the manifest's icons exist at their declared
  sizes, including a maskable one; the page links them; the server serves the
  manifest as `application/manifest+json` and revalidates the service worker;
  the service worker stores only the offline page and handles only navigations.
- `tests/test_browser_architecture.py`: no colour literal outside the theme
  blocks, and no variable used that is never defined.
- `frontend/tests/theme.test.ts`, `wake_lock.test.ts`, `talk_button.test.ts`,
  `a11y.test.ts`, `turn_detection.test.ts`: following the device, the lock and
  its minute, each hold behaviour, announcements, focus, the dialog and chat
  list, and the cancel during start.
- Not verified here: installing on a real phone, the lock on real hardware, and
  a real screen reader. Item 21 in BACKLOG section 4.
