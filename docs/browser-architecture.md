# Browser architecture

## Source and build boundary

Browser source lives under `frontend/src`. Vite emits deterministic production
assets into `web`, which is the only browser directory included in the Python
package and final runtime image. `web/app.js` and `web/styles.css` are generated;
changes begin in TypeScript/CSS source and are committed with the rebuilt files.
Shared form-control tokens in `frontend/src/styles.css` own input, textarea,
native-select, option, placeholder, and file-picker colors. Both themes declare
their native `color-scheme`; individual settings screens must not depend on a
browser-default white control or dropdown background.

Use Node.js 24 and the pinned lockfile:

```bash
npm ci
npm run frontend:typecheck
npm run frontend:test
npm run frontend:build
npm run frontend:e2e
```

## Module responsibilities

- `api.ts`: authenticated `/api/v1` transport, safe errors, SSE frame parsing,
  replay cursor, job cancellation, and protected artifact URLs.
- `state.ts` and `types.ts`: one typed application state and legal client phase
  transitions. Illegal transitions throw rather than create ambiguous UI state.
- `routing.ts`: hash-based home/chat/settings navigation with no server-side
  route ownership.
- `chat.ts` and `chat_rendering.ts`: turn submission, delta rendering, terminal
  reconciliation, persisted transcript refresh, and message/media presentation.
- `chat_drawer.ts`: chat search, selection, individual rename/hide, and atomic
  bulk hide or permanent-delete workflows.
- `client_id.ts`: transient optimistic-message identifiers. It uses
  `crypto.randomUUID` when available, `crypto.getRandomValues` on direct-LAN
  HTTP, and a non-security monotonic fallback only when Web Crypto is absent.
- `capabilities.ts`: durable model-request cards, explicit approval/denial,
  progress polling, cancellation, and protected result rendering.
- `settings.ts` and `settings_view.ts`: canonical settings envelope,
  normalization, Memory v2 review, personas, workspaces, explicit memory bulk
  actions, and composition of focused settings views.
- `everyday_settings_view.ts`: goal-oriented General, speech, media-default, and
  User screens without provider workflow ownership.
- `model_settings_view.ts`: effective conversation-model state, common sampling
  defaults, closed expert controls, and per-model runtime overrides.
- `task_model_settings_view.ts` and `media_catalog_settings_view.ts`: durable
  platform-role and media-resource editors, refresh conflict protection,
  readiness checks, plan previews, and content-free audits.
- `operations_settings_view.ts`: administrator-only GPU coordination and backup
  operations, including explicit provider-control authorization and restore
  verification.
- `settings_contracts.ts`: the small dialog boundary shared by focused settings
  views without coupling them back to the application shell.
- `settings_controls.ts` and `settings_ui.ts`: accessible label/control wiring,
  common cards, closed advanced disclosure, truthful readiness rows, and
  information tips revealed by pointer hover or keyboard focus.
- `identity_settings_view.ts`, `identity_settings_components.ts`, and
  `identity_media_picker.ts`: guided persona reference setup, separate truthful
  generation/verification readiness, owner-protected thumbnail selection,
  optional verifier configuration, validation, and audit history.
- `media.ts`, `recording.ts`, `playback.ts`, and `visualization.ts`: async media
  jobs, push-to-talk transcription, completed-file speech playback, and real
  playback-driven visualization. While a turn or direct media job is active,
  the composer replaces Send with an enabled Cancel action wired to the durable
  job endpoint. Acknowledged media cancellation returns to `idle` without being
  presented as generation failure.
- `app.ts`: composition root, shell/onboarding/auth views, routing coordination,
  session expiry, and visible reporting of unexpected browser errors.

## Turn behavior

Submitting a message creates a durable turn/job and enters `queued`, then
`thinking` when the stream starts. `assistant.delta` events update a temporary
message. A terminal event is reconciled with durable chat/job state before the
client returns to `idle`. SSE loss does not imply cancellation; the client may
poll final state. Only `DELETE /api/v1/jobs/{id}` cancels work.

Direct-LAN HTTP remains supported for typed desktop chat even though it is not a
browser secure context. Client-only reconciliation IDs must therefore never
assume `crypto.randomUUID` exists. Durable IDs, idempotency, authentication, and
authorization continue to come from the server.

Current speech playback still uses completed authenticated audio files. The
state machine deliberately exposes `recording`, `transcribing`, and `speaking`
phases so later realtime voice can extend the contract. During `speaking`, the
composer remains editable; sending typed text or starting hold-to-talk stops the
current file playback before beginning the new turn. This is explicit manual
interruption, not streaming speech, VAD, or automatic realtime barge-in.

Platform-planned media capabilities never open a browser confirmation modal as
an ephemeral side effect. The browser reloads owner-scoped capability requests with each chat
and renders their durable state beneath the assistant message. Approval starts
the linked job; denial and cancellation survive reloads. Each model-requested
card renders its immutable media plan before approval, including selected
resources, explanation, estimates, warnings, and blocked/stale state. Existing
user-clicked media actions use the same backend service but need no second
confirmation. The operator-only Settings surface edits explicit resource
metadata and previews semantic plans; it is not a persona-facing model lab.

Visual identity remains operator reviewed. Browser state distinguishes profile,
consent, reference, validation, and claim states; it never converts a provider
error into a verified badge. Reference uploads use canonical authenticated
multipart transport and protected image URLs. User-facing selection uses an
owner-scoped media listing and thumbnails instead of requiring internal media
IDs. Advanced controls explain that verification evaluates a result but does not
condition generation. The same information-tip component is used for Visual
Identity, everyday, and operator settings; safety consequences and
destructive-action warnings remain visible rather than being hidden in a
tooltip. Operator resource editors are collapsed by default and use named
summaries, while nested raw payloads, budgets, and content-free audits remain
available to experienced operators.

## Test boundary

Vitest covers pure browser contracts in jsdom. Playwright intercepts canonical
API requests with deterministic fixtures and exercises complete browser
journeys without provider credentials. Python API tests separately prove the
real FastAPI contracts, ownership, migration, and persistence. The process smoke
then runs generated browser assets and canonical APIs against a real Uvicorn
process plus fake Ollama.
