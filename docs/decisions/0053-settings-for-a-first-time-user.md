# ADR 0053: Settings a first-time user can find their way through

- Status: accepted
- Date: 2026-10-29
- Owners: Nice Assistant maintainers

## Context

The owner asked whether somebody running the container for the first time could
work settings out unaided, having long found the section hard to navigate. A walk
through a real server on an empty data folder answered it. Getting to a typed
conversation worked. Everything past that was a dead end.

- Every optional ability - spoken replies, transcription, pictures, video -
  started Off, and its page said nothing about what would switch it on.
  "Local service" opened an empty address box. Image Generation's one visible
  line was "Image generation is off." under a dropdown that already said Off.
- Controls offered what could not happen. "Speak replies aloud" was ticked with
  nothing to speak. A persona was "Allowed to send pictures" with nothing to
  make them. Hold to Talk ended in a red banner, "Enable OpenAI STT in
  Settings", which pointed at the cloud and at a page name that no longer
  existed. The homepage showed overnight pictures disabled, "Turned off for
  this deployment", on every fresh install.
- The picture pages disagreed. Image Generation offered Automatic1111 first,
  while Media Catalog only finds ComfyUI models and only said "ComfyUI is not
  reachable" with nowhere to fix it.
- The everyday pages saved through a button in the header. A change made
  without it looked applied - a theme switched at once - and was gone after a
  reload.
- On a phone the section menu was a sideways strip of fourteen names with no
  groups, and the open page's name could be scrolled out of sight.
- GPU Coordination showed red "Unavailable" badges for services nobody had set
  up. Providers were "Openai" and "Comfyui", and a local service on another
  box was "on this machine".

The earlier passes (2026-08-26 to 2026-09-03) fixed how crowded each page was.
A newcomer's problem was direction, which those passes did not address.

## Decision

**A control that cannot work yet is the way to make it work.** In its place goes
one row naming the thing and a "Set up" link to the page that keeps it
(`setupRow` and `setupLink` in `settings_page.ts`). The talk button becomes "Set
up talking". Spoken replies on the homepage, the General page and the persona
page, pictures on the homepage and the persona page, and video without ComfyUI
all follow the same rule. Overnight pictures is left off the homepage until
pictures exist and the deployment allows them. Inside settings the link goes
through the section menu's guard, so unsaved work on a persona still asks.

**Off says what is needed; on reads like a connection.** An Off page's one
visible line names what would switch it on. Chosen, the page runs in the order
it can be done. First the service, then its address with an example ("e.g.
http://your-server:8188"), then Test straight under it, then what needs it. The
OpenAI key appears where OpenAI is chosen, as the same stored value Your profile
edits. Shape and blur wait until pictures are on.

**ComfyUI first.** It is the local image service a new install assumes, and the
first one offered. The default is one constant on each side of the wire.
`DEFAULT_LOCAL_IMAGE_BACKEND` in `app/media.py` replaces four separate
"automatic1111" fallbacks, and `tests/test_local_image_default.py` fails the
build if it and `frontend/src/settings.ts` drift. A stored choice is kept, and
every browser save stores one. With ComfyUI chosen, the catalog's model finder
sits under the connection on Image Generation. Until pictures point at ComfyUI,
Media Catalog says where to start instead of offering a finder that can only
fail.

**Settings save as they change.** The everyday pages save themselves a moment
after a change. The header says "Saving…", "Saved" or "Not saved" beside Close.
Close saves at once. A save that answers after a newer change does not
overwrite it, and the newer change is saved straight after. A page about one
thing keeps its own Save and its leave guard: a persona, a model or recipe in the
catalog, a background role, GPU coordination. Each is a document
or changes another service's state, and one Save per thing is the rule there.
"Reset to Default" asks first, sits at the end of the page, and is not offered on
Personas or Workspaces.

**On a phone, a list and then a page.** `#/settings` is the list, with its groups.
Choosing a page replaces it, and "‹ Settings" goes back. Opening settings lands
on the list, and a desktop shows the menu beside General as before. "System" is
renamed "Advanced".

**Names said plainly.** Providers are spelled as their makers spell them. A local
service is "on your network" and a cloud one "leaves your network". A service
nobody pointed anything at reads "Not in use" in grey. The timezone is chosen
from the zones the browser knows. A single workspace is not asked to be the
default.

**The first load is not a race.** A new account asks for the catalog and for
picture readiness together. Both used to insert the catalog settings row, and
the loser answered 500. The row is inserted only when absent and then read back.

## Alternatives considered

- **A setup wizard after the first-run page.** It front-loads three services a
  newcomer may never want, and a wizard is a second copy of the settings pages
  that drifts from them. The link sits where somebody notices the gap instead.
- **Hiding the talk button with nothing to transcribe.** Quieter, but then
  nobody learns the product can be talked to. The button keeps its place and
  says what it needs.
- **Autosave everywhere, including a persona's page and the catalog.** A
  persona, a recipe and a role each have a leave guard that already works, and
  a recipe's definition or a character card is authored text, not a switch.
  Converting them would trade a guard people understand for a debounce they
  cannot see, for no newcomer benefit.
- **Keeping Automatic1111 as the default.** It would keep the catalog, recipes
  and identity work - which only speak ComfyUI - one hidden choice away from
  anybody new.

## Consequences

Opening settings from a chat lands on the list rather than the last page
visited. A change on an everyday page reaches the server within a second without
being asked, so there is no "cancel" short of changing it back or Reset to
Default. Docs and the homepage now say "your network" where they said "this
machine". The internal names in `app/data_locality.py` are unchanged.

## Verification

- `frontend/tests/settings_autosave.test.ts`: a change saves after a pause with
  no Save button; a change made during a save is kept and saved after it; Close
  saves at once; reset asks first; `#/settings` is the list and "Settings" goes
  back to it.
- `frontend/tests/everyday_settings.test.ts`, `talk_button.test.ts`,
  `persona_settings.test.ts`, `media_catalog.test.ts`,
  `resource_coordination.test.ts`: each control that cannot work is a link
  to the right page; the Off pages say what they need; addresses carry an
  example and Test follows them; the OpenAI key appears where OpenAI is chosen;
  ComfyUI comes first and its finder replaces the filename box; the catalog
  points to the pictures page; an unused service is not a fault.
- `tests/test_local_image_default.py`, `tests/test_catalog_settings_first_load.py`:
  the default is pinned once and shared; the request that loses the first-load
  race reads the winner's row (it fails without the fix).
- `frontend/e2e/app.spec.ts`, desktop and phone: the homepage's set-up link
  reaches Spoken replies, overnight pictures stays away when forbidden, and every
  settings journey steps back to the list on a phone.
- A real server on an empty data folder, screenshotted desktop and phone: a
  theme chosen on General survives a reload, and the first load has no server
  error.
- Not verified here: connecting real Kokoro, Wyoming and ComfyUI services by
  following only what the pages say. Item 23 in BACKLOG section 4.
