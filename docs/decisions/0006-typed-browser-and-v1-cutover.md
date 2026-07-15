# ADR 0006: Typed browser and canonical API cutover

- Status: accepted
- Date: 2026-07-13
- Owners: Nice Assistant maintainers

## Context

The browser was a 3,230-line JavaScript monolith mixing transport, persistence
shapes, rendering, settings, audio, media, and state. It used a broad `/api`
compatibility router retained after the ASGI migration. That duplicated public
contracts, let browser and canonical response shapes drift, and made later
realtime voice state unsafe to extend. Saved assistant messages also embedded
filename-based legacy media URLs, so removing the routes without data migration
would break existing conversations.

## Decision

Use strict TypeScript and Vite with focused browser modules and an explicit
client phase state machine. Product source calls only typed `/api/v1` contracts.
Build deterministic packageable assets into `web`, and run typecheck, Vitest,
build, and Playwright in the canonical verifier and CI.

Remove the broad `/api` compatibility router once the browser cutover passes.
Migration `0007_browser_v1_cutover` rewrites stored image/video URLs in messages,
summaries, and job results to `/api/v1/media/{media_id}`. New media generation
persists canonical protected links directly. Missing static files return 404
instead of the application shell.

## Alternatives considered

- Incrementally split untyped JavaScript: rejected because it would preserve
  unchecked wire contracts and delay discovery of real response-shape drift.
- Keep `/api` adapters indefinitely: rejected because they become a second
  public API and weaken removal pressure.
- Remove legacy media routes without migration: rejected because historical
  assistant artifacts would stop loading.
- Introduce a component framework now: deferred because module/state boundaries
  and contract safety are the foundation needed before a visual redesign.

## Consequences

Node.js is now a required development and container-build dependency, while the
runtime remains Python-only. Generated assets must be committed with their
source. The only supported application API namespace is `/api/v1`; old clients
must upgrade with the browser. Historical media keeps owner-scoped access after
migration. Realtime voice remains a later WebSocket design and cannot infer
streaming or interruption from the current completed-file playback modules.

## Verification

- Strict TypeScript compilation and Vite production build.
- Vitest coverage for state, settings, transport/SSE, media, routing, and safe
  rendering.
- Playwright journeys for onboarding/login, streamed chat, settings, memory,
  and media, with an assertion that no legacy API is requested.
- Migration/API tests for preserved media links, canonical fields, ownership,
  and removed legacy routes.
- Real-process smoke for generated assets, canonical chat/jobs/cancellation,
  protected media, backups, and clean shutdown.
