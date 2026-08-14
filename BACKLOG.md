# Backlog

Work queue for autonomous sessions. Take the top unblocked item from **Ready**,
finish it, verify, and merge. Record assumptions and questions in the sections at
the bottom rather than stopping to ask.

Blocked items are listed with what unblocks them so nothing is silently dropped.
Decisions taken without sign-off go in `docs/autonomous-decision-log.md`.

## Ready

Ordered by value. Nothing here needs the operator.

The first three items below were verified against `main` at `0df1d89` on
2026-08-14. Treat the reproductions as regression-test inputs, not just review
notes.

### 1. Make chat workspace and persona bindings immutable

**Why this is first:** a chat can currently be retargeted to another persona or
workspace after it already has a transcript. The old persona's assistant replies
then remain in the next model prompt. A cross-workspace persona can also be saved
onto a chat before the next turn fails with `persona not found`. That makes the
conversation boundary internally inconsistent and can leak one persona's context
into another.

**Required work:**

- Make chat creation the authoritative point where `workspace_id` and
  `persona_id` are bound. A turn must never rewrite either field.
- During a compatibility window, turn payloads may repeat the chat's current IDs,
  but reject mismatches before writing the user message, turn, job, or chat.
  Remove the redundant fields from the browser request and document their API
  deprecation/removal.
- Stop `PATCH /api/v1/chats/{id}` from retargeting an existing chat. In the
  browser, selecting a different persona or workspace while a chat is active must
  create a clean chat with the new binding. Do not silently copy the old
  transcript; an explicit fork feature is separate future work.
- Validate persona/workspace membership atomically at chat creation and in one
  reusable application-service boundary. Do not rely on a later context lookup to
  discover an invalid combination.
- Add a data-preserving migration or compatibility repair for pre-existing
  inconsistent chats. Keep their transcripts readable and document the
  conservative rule used; do not delete or silently reattribute messages.
- Record the binding decision in an ADR and update the conversation, API,
  browser, migration, security, and testing documentation it affects.
- Do not merge `codex/human-experience-realignment` wholesale. Port only useful
  concepts: that branch is divergent and its `0019_memory_v3_identity_access`
  migration conflicts with the current `0019_persona_character_card` lineage.

**Acceptance evidence:**

- A cross-workspace persona update is rejected and leaves the stored chat
  unchanged.
- A per-turn workspace/persona mismatch is rejected before any durable row or
  event is created; repeating the bound IDs remains compatible if retained.
- Changing persona in the UI starts a new chat, and no prior persona system
  material, lore, memories, user messages, or assistant messages enters its
  prompt.
- Existing valid chats remain readable and can continue normally after migration;
  inconsistent legacy rows follow the documented non-destructive rule.
- Focused service/API/browser tests cover the two verified reproductions, followed
  by the complete verifier and a deterministic human-experience scenario.

### 2. Make Task Model readiness credential-aware and truthful

**Observed failure:** an OpenAI Task Model profile can be saved through the API
without an account API key, yet readiness reports `ready: true`. Execution then
fails with `openai_api_key_missing`. The settings UI currently offers only Ollama,
so adapter presence, account configuration, UI support, and runtime readiness are
being conflated.

**Required work:**

- Pass the account/user context into provider-attempt readiness. For OpenAI, a
  missing or blank account API key must make the attempt unavailable with a safe,
  actionable message; never echo the key.
- Separate "adapter is installed", "credentials are configured", and "a live
  request has been verified" in naming/status text. `health()` must not say
  "Configured" when it has no account credential evidence.
- Preserve fallback semantics: a keyless OpenAI primary may report
  `fallback_ready` only when the configured fallback really is ready.
- Do not expand OpenAI into the Task Model settings UI as part of this fix. The
  product/privacy decision in Open question 5 determines whether a later slice
  exposes it or keeps it as a contract adapter only.
- Update task-model, settings, security, testing, and debt documentation so an
  installed adapter is not advertised as a usable provider.

**Acceptance evidence:**

- A saved keyless OpenAI profile returns `ready: false` (or genuine
  `fallback_ready`), and an actual run fails/falls back with the same reason.
- Blank and redacted-key cases, valid configured-key status, missing model,
  provider failure, and fallback combinations have deterministic tests.
- API and browser labels do not imply that OpenAI Task Models are selectable or
  live-verified when they are not.
- Focused task-provider/service/API tests and the complete verifier pass.

### 3. Untangle the conversation critical path before extending voice

**Why now:** `ConversationService.create_turn` has cyclomatic complexity 49 and
combines binding resolution, model/settings selection, persistence, job creation,
context inputs, and follow-up work. `ContextService.plan` is another grandfathered
critical path. Both will be touched by streaming speech, interruption, and
turn-taking, so leaving them tangled raises the cost and risk of every voice step.

**Required work:**

- After item 1 establishes the binding invariant, extract small application
  services/helpers for turn validation and resolution, transactional persistence,
  generation-job construction, and post-turn follow-up scheduling.
- Split context loading, protected-section budgeting, optional-section selection,
  transcript-floor selection, and final prompt assembly without changing their
  ordering or truthful context notices.
- Keep HTTP routes, provider adapters, persistence, and event delivery separate.
  This is a behavior-preserving refactor, not a new orchestration framework.
- Remove the `# noqa: C901` exemptions from `create_turn` and
  `ContextService.plan`; do not add new complexity exemptions elsewhere.
- Update architecture/debt documentation and add narrow characterization tests
  wherever existing behavior is not already pinned down.

**Acceptance evidence:**

- Both target functions satisfy the repository complexity ceiling of 15 without
  suppressions.
- Prompt order, history floor, memory/lore selection, title generation, context
  notices, job/event ordering, cancellation, fallback, and recovery behavior are
  unchanged in focused tests.
- The complete verifier and deterministic human-experience scenarios pass.

## Blocked

| Item | Blocked on |
|---|---|
| Voice core steps 10–13 | Step 10 is a blind **listening** evaluation — human ears, not code. The roadmap also gates the block on production acceptance, which needs the deploy. |
| Installed acceptance for roadmap 22 and 23 | Running the merged image on the real hardware |
| Deployment guard live migration (roadmap 24) | One supervised session on the server |
| Identity stage latency and capacity acceptance | Real verifier, consented references, and a compatible ComfyUI workflow deployed |
| Workspace-shared lore | Product decision — see `docs/autonomous-decision-log.md` D5 |
| OpenAI as a user-selectable Task Model provider | Explicit privacy/product decision in Open question 5, followed by provider-specific model discovery, disclosure, credential UX, and live acceptance. The Ready correctness fix does not expose it. |
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
- **Merging the old Memory v3 branch wholesale.** Its useful immutable-binding
  idea is captured in Ready item 1, but the branch has materially diverged and
  reuses migration number `0019` for a different schema. Grants stay deferred
  unless a concrete multi-user/access-control need overturns the preceding item.
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
5. May Task Model roles send conversation-derived text to OpenAI, or must these
   roles remain local-only? Until explicitly approved, the UI remains local-only
   and must not advertise OpenAI as selectable.
6. When the same persona is linked to multiple workspaces, should that persona's
   approved memories follow it across workspaces, or should workspace plus persona
   be a hard intersection? Current persona-scoped memories follow the persona;
   changing this later would require a migration and clear UI wording.
