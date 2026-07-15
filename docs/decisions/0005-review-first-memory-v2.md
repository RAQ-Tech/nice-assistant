# ADR 0005: Review-first durable Memory v2

- Status: accepted
- Date: 2026-07-13
- Owners: Nice Assistant maintainers

## Context

Legacy memory rows contained only owner, tier, text, and creation time. The
browser treated chat scope as if it meant pending status, delete was destructive,
workspace/persona removal hard-deleted related rows, and context loaded a recent
set without lexical relevance. Automatically promoting extracted facts would
have repeated the original silent-memory defect.

## Decision

Memory is an audited lifecycle with pending, active, rejected, forgotten, and
superseded states. Conversation extraction runs only after the visible turn has
committed and creates pending rows linked to the source turn/message. Manual
saves are active; edits create superseding revisions; forget is a reversible
state change. Only active rows are retrievable.

SQLite FTS5 supplies local lexical relevance inside strict owner and scope
filters. Recent active rows fill the bounded result set when lexical overlap is
sparse. Exact normalized live duplicates are constrained per owner and scope.

The current chat provider performs best-effort structured extraction in a
separate durable job. Its failure cannot alter the completed turn. Candidates
require explicit user approval and therefore never silently become prompt
context.

## Consequences

Memory provenance, review, revisions, lifecycle history, and undo are inspectable.
The additional extraction call consumes provider time after eligible turns, but
does not block chat completion and is lower priority than queued interactive
turns. FTS5 is lexical rather than semantic; this is truthful and sufficient for
the SQLite/private-LAN baseline. Rejected and forgotten records increase storage
until a later retention policy is approved or the owner explicitly and
permanently deletes them under ADR 0015.

## Verification

Tests cover legacy migration and exact-duplicate supersession, constraints, FTS
population/ranking, status-only retrieval, candidate provenance, nonblocking
post-turn extraction, approve/reject/forget/undo, revision supersession, owner
isolation, extraction failure, scope archival, browser labels, and compatibility
routes.
