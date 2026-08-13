# Backlog

Work queue for autonomous sessions. Take the top unblocked item from **Ready**,
finish it, verify, and merge. Record assumptions and questions in the sections at
the bottom rather than stopping to ask.

Blocked items are listed with what unblocks them so nothing is silently dropped.
Decisions taken without sign-off go in `docs/autonomous-decision-log.md`.

## Ready

Ordered by value. Nothing here needs the operator.

1. **Show context degradation in the conversation.** A turn already records
   `context_degraded_reason` and the API returns it, but no browser code reads it.
   When the history floor drops the summary or saved memory to protect the
   conversation, the person talking has no way to know their assistant is running
   with less context than usual. `docs/conversation-context.md` claims turn
   diagnostics are exposed to the owner; today that is true only of the API.

2. **Tune memory extraction for precision.** The original complaint was noisy
   pending memories, not forgotten ones. The scope fix removed the worst of it,
   but extraction still proposes freely. Raise the confidence floor, tighten the
   extraction contract toward durable facts, and extend the developer evaluation
   cases that screen inclusion against exclusion. Precision over recall is the
   stated preference; the code does not yet express it.

3. **Add a small always-present owner profile block.** A short authored block
   about the account holder, pinned in context the way the character card is.
   This is what the major assistants converged on, and it does more for felt
   continuity than a larger retrieved corpus. It is affordable now that the
   history floor exists, and it should be capped the same way the card is.

4. **Give lore entries alias lists.** Keyword matching misses paraphrases. Aliases
   stay literal strings — no embeddings, no new service — and close the common
   case where one entry needs `sister`, `sibling`, and a name. Cheaper and more
   debuggable than semantic retrieval.

5. **Decide an expiry policy for rejected and forgotten memories.** Retention is
   durable and unbounded, with no automatic expiry. Users can delete explicitly,
   but a private deployment accumulating rejected candidates forever is a
   sensitive-data question, not just a storage one.

6. **Make turn event replay durable.** Replay is bounded and process-local, so a
   restart mid-turn loses the event stream even though the turn itself survives.

7. **Second Task Model adapter.** Ollama is the only adapter. A second one proves
   the structured-output contract is genuinely provider-neutral rather than
   Ollama-shaped.

## Blocked

| Item | Blocked on |
|---|---|
| Voice core steps 10–13 | Step 10 is a blind **listening** evaluation — human ears, not code. The roadmap also gates the block on production acceptance, which needs the deploy. |
| Installed acceptance for roadmap 22 and 23 | Running the merged image on the real hardware |
| Deployment guard live migration (roadmap 24) | One supervised session on the server |
| Identity stage latency and capacity acceptance | Real verifier, consented references, and a compatible ComfyUI workflow deployed |
| Workspace-shared lore | Product decision — see `docs/autonomous-decision-log.md` D5 |
| Live 12 GB timing and capacity tuning | The hardware |

## Deliberately not doing

Recorded so they are not rediscovered as gaps.

- **Adopting mem0, Zep, Letta, or Cognee.** They optimize recall; the complaint
  was noise. Each adds a service and an embedding model to a GPU budget already
  under contention, and none provides per-persona access boundaries.
- **Semantic or vector lore retrieval.** Same reason. Keyword matching is
  predictable and debuggable, and the preview route makes it tunable.
- **Grants, principals, and multi-tenancy for memory.** One human and a handful of
  personas. Persona scoping already delivers the isolation that design was for.
- **Document ingestion.** Chunking, versioning, citations, and retrieval is a
  larger product than everything else on this list combined.
- **Autonomous persona life simulation.** Generated backstory and off-screen
  events are explicitly out of scope in the persona depth spec.

## Assumptions

Choices made where the request was ambiguous. Overturn any of these freely.

- Precision beats recall for memory. A missed fact is cheaper than a wrong one
  entering context as truth.
- The conversation is the product. Optional prompt material yields before
  conversation history does, at every window size.
- The platform decides, the model proposes. No task model selects scope,
  resources, or which lore fires.
- A setting that saves must change runtime behavior, or it must be refused at
  save time rather than silently substituted later.
- Persona material is authored, never generated or inferred from conversation.

## Open questions

For the operator, when convenient. None of these blocks the Ready list.

1. Should lore be shareable across personas in a workspace, or stay
   persona-scoped? Currently persona-scoped.
2. After the deploy, is 8k context affordable alongside speech and image
   generation on the 12 GB card? Measured behavior beats the estimate.
3. Is there an appetite for a second chat provider, or is Ollama the permanent
   local boundary?
4. Should rejected and forgotten memories expire automatically, and after how
   long?
