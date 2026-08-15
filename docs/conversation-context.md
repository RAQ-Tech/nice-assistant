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

Only completed prior turns enter a later model prompt. Failed and cancelled
turns remain visible in the durable transcript and diagnostics, but their
unanswered user text is not replayed as a second instruction on the next turn.
This prevents a recovered provider from answering an abandoned request instead
of the user's current message.

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

Conversation history keeps a floor of 25 percent of the prompt budget. When the
assembled prompt would leave less, droppable sections yield in reverse authority
order — summary, then saved memory, then persona lore, then persona example
dialogue — until the floor is clear. Nothing yields on a turn with no history, protected sections
never yield, and a turn that dropped sections is marked degraded and reports the
dropped material as omitted rather than included.

An authored owner profile is protected material sent with every turn, rendered
after persona instructions and labeled as factual context rather than
instructions. It carries the account display name when one is set. Because
protected material fails a turn instead of degrading, it is capped at 10 percent
of the prompt budget when it is saved rather than clipped when a turn is planned,
and it never reaches the summary, memory-extraction, or capability roles.

A persona character card is protected material capped when it is saved. Persona
example dialogue is droppable data under a 10 percent allowance, rendered above
saved memory, labeled as voice examples rather than transcript, and included as
whole exchanges or not at all. It is assembled into the persona prompt only and
never reaches the summary, memory-extraction, or capability roles.

Persona lore is droppable data under a 12 percent allowance, rendered between
example dialogue and saved memory. Entries are selected by deterministic literal
keyword matching over the current message and the last three transcript messages;
no model chooses which fire, keys are never treated as patterns, and injected
lore is not rescanned, so activation cannot cascade. A key also matches its common
English plural unless the entry turns that off; forms are generated from the authored
key and never stripped from the message, so matching only widens where the operator
already pointed it. Fired entries sort by
priority, then recency, then identifier, and are included whole or skipped.

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
owner. A turn that ran with reduced context also reports its reason on the
assistant message it produced, so the conversation itself says so after a reload
rather than only the diagnostics API. Prompt text is not copied into logs or diagnostic metadata.
