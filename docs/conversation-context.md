# Conversation context policy

Nice Assistant prepares a bounded prompt when a queued turn starts, not when it
is submitted. Turns in one chat execute in durable sequence; separate chats may
run concurrently. Later queued user messages are outside an earlier turn's
context boundary.

## Chat identity and access boundary

Every new chat is permanently bound to one explicitly chosen persona and one
explicit context: personal, or a named workspace in which that persona is a
current member. The API and browser do not silently select a persona or infer a
workspace. Persona/workspace IDs supplied to deprecated update or turn fields
cannot rebind the chat; a different identity requires a new chat.

The binding is stored independently of model choice. Selecting another model
for a later turn, restarting Ollama, or restarting Nice Assistant does not
change the chat's persona or access context. Current persona/workspace names are
resolved for display while the immutable IDs and original name snapshots remain
durable.

Chats migrated from before this contract are `legacy_unresolved`: their
transcript remains readable, but they cannot continue. An otherwise active chat
also becomes non-continuable if its human principal, persona, or workspace is
gone, or if its persona is no longer a member of the bound workspace. The
service checks this before writing a turn and again when queued work starts,
before provider invocation. Chat-bound title, capability-planning, and
memory-extraction follow-ups repeat the same binding check immediately before
their external model call and again before applying a result.

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

## Memory and deduplication

`saved` uses active memories and enables post-turn candidate extraction; `off`
uses none and creates no candidates. The old `auto` and `manual` values migrate
to `saved`. Extraction creates pending review rows after conversation completion
and grants each new candidate only to the immutable source persona, including
inside a workspace chat. The extractor cannot choose access and Phase 2 never
automatically activates a candidate.

Memory comparison uses Unicode normalization, case folding, and whitespace
collapse. Exact duplicates for the same source persona are skipped; revision
edits preserve a supersession chain. Fuzzy matching is intentionally avoided,
and verbatim transcript duplicates are not injected.

Retrieval derives authorization from the durable chat binding rather than from
mutable request parameters. It verifies the human principal, current persona,
workspace existence/membership when applicable, active unrevoked
persona/workspace grants, lifecycle state, and temporal validity before lexical
FTS matching or recency fallback. Authorized FTS matches use deterministic
recency ordering rather than BM25 statistics from the global memory corpus, so
an unauthorized persona or owner cannot affect prompt selection. A personal
chat receives persona-granted memory only. A workspace chat may also receive
memory granted to that workspace, which means a persona added to the workspace
later can use its existing workspace-granted knowledge. Removing that
membership removes access immediately.

Pending, rejected, forgotten, superseded, stale, expired, completed, and
cancelled memories never enter prompts. Existing pre-v3 rows are retained as
`legacy_quarantined` with no grants and are likewise excluded.

The universal owner profile is deliberately separate from ordinary memories.
Its explicitly populated allowlisted values are added to a labeled profile
block for every valid persona, including when ordinary saved memory is `off`.
The extractor cannot populate or alter it.

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
