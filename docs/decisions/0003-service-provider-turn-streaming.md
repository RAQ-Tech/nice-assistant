# ADR 0003: Service, provider, and streamed turn architecture

- Status: accepted; compatibility portion superseded by ADR 0006
- Date: 2026-07-13
- Owners: Nice Assistant maintainers

## Context

The public FastAPI process still depended on a raw HTTP monolith through a
loopback bridge. Provider work, SQL, queue state, prompt assembly, and transport
formatting were coupled. Chat jobs did not have a durable turn identity, the
current user input was duplicated in Ollama prompts, provider exceptions could
become assistant text, and cancellation could not reliably prevent late results.

The existing browser must continue working while the typed client is deferred to
Step 9. Text streaming is needed now, but bidirectional voice remains Step 13.

## Decision

Nice Assistant runs one dependency-injected FastAPI application. Typed `/api/v1`
routes and direct `/api` compatibility adapters call the same application
services. Repositories and units of work own SQLAlchemy sessions; routes perform
transport validation and authentication only.

Each chat request atomically persists a user message, queued
`conversation_turn`, and linked `async_job`. `ConversationService` owns prompt
assembly, provider invocation, and successful assistant persistence. `JobService`
alone owns legal state transitions, separate queue lanes, cancellation tokens,
linked job/turn transactions, and late-result rejection. Unfinished state becomes
failed with a safe restart reason during startup.

Providers implement `ChatModelProvider` or `MediaProvider` contracts with
normalized health, timeout, cancellation, artifacts, and redacted errors. Ollama
uses streamed NDJSON from `/api/chat`. Existing media integrations are wrapped,
while modeled residency is excluded from readiness.

Authenticated text events use SSE. Every subscriber receives a current snapshot,
then bounded in-process replay selected by `Last-Event-ID`, then live events until
one terminal event. SSE disconnect never implies cancellation.

## Alternatives considered

- Keeping the loopback raw handler would preserve two lifecycle and routing
  systems and prevent service ownership from becoming enforceable.
- Migrating the browser and backend together would couple this foundation change
  to the Step 9 TypeScript/state rewrite.
- Persisting every delta as an event log would add write amplification and a new
  retention/data model before any product requirement for cross-restart replay.
- Making SSE disconnect cancel work would turn ordinary network changes into
  destructive conversation state changes.

## Consequences

The prompt contains the current user message exactly once. Provider failures are
safe terminal state and never assistant messages. Final state survives restarts,
while delta replay is intentionally process-local. Cancellation is idempotent and
durable, but is cooperative at the provider boundary; adapters unable to interrupt
may continue work whose output is discarded.

Step 9 removed the direct `/api` adapters after the typed browser and stored-media
link migration passed; see ADR 0006. Permissioned capability routing, streaming
speech, and truthful external capacity control remain Steps 14, 11-13, and 15.

## Verification

- Migration tests preserve existing chats, messages, jobs, and media through the
  0004 SQLite reconstruction and enforce legal states.
- Unit/contract tests cover state transitions, linked state, safe failures,
  single-input prompt construction, cancellation, Ollama NDJSON, and SSE replay.
- API parity tests exercise browser-used `/api` behavior and owner isolation on
  isolated FastAPI applications without a second listener.
- Three consecutive coverage-enforced suites, process smoke, Docker build, and
  installed-container smoke must pass before publication.
