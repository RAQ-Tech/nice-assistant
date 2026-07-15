# ADR 0004: Causal bounded context and durable summaries

- Status: accepted
- Date: 2026-07-13
- Owners: Nice Assistant maintainers

## Context

Step 6 made turns durable but assembled prompts before queued jobs ran. A later
turn could therefore miss its predecessor's assistant response. Context used a
fixed message count, loaded unbounded memories, silently wrote transcript turns
as memories, and had no durable compaction boundary or provider allocation.

## Decision

Conversation jobs use a per-chat ordering key and build prompts at execution
time from the durable turn sequence. A `ContextService` owns provider-aware
allocation, conservative estimation, precedence, exact deduplication, bounded
selection, incremental summarization, and accounting.

Conversation summaries are append-only checkpoints. Turns reference the exact
checkpoint used. Nice Assistant explicitly sends its resolved Ollama `num_ctx`;
provider maximum metadata clamps but does not select the allocation.

Canonical memory modes are `off` and `saved`. Legacy `auto`/`manual` values map
to `saved`, automatic writes stop, and uncertain legacy memories are preserved.

## Consequences

Same-chat turns are causal with multiple workers while independent chats remain
concurrent. Long chats have bounded prompts and auditable compaction. Summary
generation can add latency occasionally, so calls are capped per turn and safe
degradation is recorded. Token estimation remains conservative until a provider
offers exact preflight counting; actual Ollama prompt usage is retained for
comparison.

Memory provenance, review candidates, FTS retrieval, supersession, and undo were
subsequently accepted in ADR 0005.

## Verification

Tests cover multi-worker ordering, independent-chat concurrency, prompt
boundaries, budgets, oversized protected content, exact deduplication, zero
automatic memory writes, summary checkpoints/fallback, migrations, owner
isolation, provider metadata, and transmitted `num_ctx`.
