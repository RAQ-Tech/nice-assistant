# ADR 0002: ASGI compatibility transition

- Status: superseded by ADR 0003 after bridge removal
- Date: 2026-07-12

## Context

The browser depends on a broad untyped `/api` surface implemented in one raw
HTTP handler. Rewriting every route and the browser in one change would obscure
contract regressions and make rollback difficult.

## Decision

FastAPI and Uvicorn own the public socket. New typed APIs use `/api/v1` and the
first resource families are implemented natively. A lifespan-managed raw HTTP
handler listens on an ephemeral loopback port solely to preserve unmigrated
`/api` browser contracts. The ASGI application forwards those calls without
exposing the bridge port to the LAN.

## Consequences

The deployment gains typed schemas, normalized errors, dependency-injected
authentication, OpenAPI, and deterministic lifespan ownership now. During the
transition, two route implementations coexist and the bridge adds local
overhead. Step 6 must migrate the remaining contracts and delete the bridge;
new features may not be added to it.

## Rejected alternatives

- A flag-day rewrite would couple every backend and browser regression.
- Leaving the raw server public until all routes move would postpone lifecycle
  and API-foundation benefits.
- Treating the bridge as permanent would preserve the monolith and create a
  false service boundary.

## Outcome

Step 6 completed the transition. Direct FastAPI `/api` adapters now call the
same services as `/api/v1`; the raw handler, loopback proxy, bridge flag, and
secondary listener were deleted. See ADR 0003 for the durable service and turn
architecture.
