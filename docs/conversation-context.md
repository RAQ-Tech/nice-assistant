# Conversation context policy

Nice Assistant prepares a bounded prompt when a queued turn starts, not when it
is submitted. Turns in one chat execute in durable sequence; separate chats may
run concurrently. Later queued user messages are outside an earlier turn's
context boundary.

## Authority and freshness

Instruction authority is application policy, persona instructions, the current
user request, prior user instructions, then summaries/memories/tool output as
contextual data. Memories and summaries are delimited and explicitly labeled as
data rather than instructions.

Factual freshness is the current user correction, timestamped tool output,
recent transcript, conversation summary, then saved memory. Tool output is a
safe durable capability result: capability key, terminal/current status,
protected artifact ID, and redacted error when present. Provider secrets,
provider request payloads, and privileged execution settings are excluded.

Prompt order is application policy, persona instructions, saved memory,
conversation summary, recent chronological turns, and the current user message
exactly once.

A prior assistant tool call and its result remain one chronological turn group
during budget selection. Pending, denied, failed, cancelled, and completed
capabilities are represented truthfully so the model does not need to guess
whether requested work ran.

Persona generation receives no platform tool schema. A separately configured
capability-planning Task Model may create semantic requests after the reply; it
cannot select providers, models, LoRAs, workflows, or identity controls.

## Budgets

The default context allocation is 4,096 tokens. A per-model setting may override
it, clamped to provider-reported model maximum when available. Nice Assistant
sends the resolved value to Ollama as `num_ctx`.

The output reserve defaults to 512 tokens. A safety reserve is the greater of
256 tokens or five percent of the context allocation. Saved memories may use up
to 15 percent of the prompt budget and a summary up to 20 percent. Conversation
history receives the remainder.

Application/persona instructions and the current request are never silently
truncated. An oversized protected request fails safely. Memories are selected or
omitted as whole entries. Recent history keeps complete newest turns first; an
individually oversized prior turn may use a labeled head-and-tail excerpt.

## Memory and deduplication

`saved` uses active memories and enables post-turn candidate extraction; `off`
uses none and creates no candidates. The old `auto` and `manual` values migrate
to `saved`. Extraction creates pending review rows after conversation completion;
pending, rejected, forgotten, and superseded rows never enter prompts.

Memory comparison uses Unicode normalization, case folding, and whitespace
collapse. Exact duplicates prefer chat, persona, workspace, then global scope;
the newest entry wins within one scope. Fuzzy matching is intentionally avoided.
Existing legacy memories are retained with honest legacy provenance. Exact live
duplicates in one scope are represented as a supersession chain, and verbatim
transcript duplicates are not injected. FTS relevance plus recency bounds the
owner-scoped candidate set before token-budget selection.

## Long-chat compaction

When projected history exceeds 75 percent of the prompt budget, the oldest
prefix is folded by the conversation-summary Task Model into an append-only
durable summary. Each checkpoint records
its predecessor, source boundary/digest, model, provider, prompt version, and
token estimate. The newest eight messages are protected when they fit.

At most two compaction calls occur during one turn. Cancellation stops
compaction. A summary-provider failure retains the prior checkpoint and uses
deterministic history truncation; the turn is marked degraded rather than failed.
Summary text is never emitted as assistant streaming output.

Turn diagnostics expose token/count accounting and the referenced summary to the
owner. Prompt text is not copied into logs or diagnostic metadata.
