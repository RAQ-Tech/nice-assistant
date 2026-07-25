# Memory v3 identity and access foundation

Memory and Knowledge v3 Phase 2 is shipped. It adds durable human identity,
immutable chat bindings, memory origins, revocable persona/workspace grants,
typed validity metadata, and an explicit universal owner-profile API. The
automatic policy remains review-first: conversation extraction creates
`pending` candidates and only `active` memories can enter a prompt. `off`
disables both retrieval and candidate extraction for that chat.

This is an identity/access foundation, not the complete v3 product. The
searchable owner control surface, grant editor, owner-profile interface,
confirmation-gated natural-language administration, grounded automatic
activation and correction, and document reference knowledge remain later
phases. No automatic candidate is activated in Phase 2.

The Phase 1 baseline tools still inspect only a supplied backup snapshot and
exercise a proposed reset only on a disposable copy. Migration to Phase 2
preserves every existing memory and does not run a live reset or deletion.

## Identity, origin, and access

Each account currently receives one durable human principal. The schema keeps
that identity separate from the login user so later multi-human support does
not require memory ownership to be inferred from a persona or chat.

Every new chat is created with one explicit persona and one explicit access
context:

- `personal`: the chat can retrieve active memories granted to its persona;
- `workspace`: the chat can retrieve active memories granted to its persona or
  to that workspace, while the persona remains a current member of the
  workspace.

The persona ID and access context are immutable for the life of the chat.
Renaming a persona or workspace changes the current display label, not the
binding. Switching models in a chat does not change it. The binding is stored in
SQLite and survives application or Ollama restarts.

Chats created before the migration are conservatively marked
`legacy_unresolved`. Their transcript stays readable, but they cannot accept a
new turn because their historical persona/access intent cannot be proven.
Likewise, deleting a bound persona or workspace, or removing the persona from a
bound workspace, leaves history readable and blocks continuation. Starting a
new chat is the only supported way to choose a different persona or context.

Memory content, origin, and access are separate:

- `memory_records` bind memory to the human principal and carry immutable
  `legacy_migrated`/`native_v3` lineage plus memory type, currentness,
  lifecycle, expiration, and last-confirmed metadata;
- `memory_origins` preserve the source chat/persona/workspace/message/turn and
  are immutable;
- `memory_grants` give access to a named persona or workspace and retain grant
  and revocation history. A grant's identity, target, source, actor, and grant
  time are append-only. Revocation is the only allowed update and is a one-way
  transition; restoring access creates a new grant rather than clearing the
  revocation. Grant events are append-only and can disappear only when permanent
  deletion cascades from the owning memory.

A workspace grant follows current workspace membership. A persona added to that
workspace later can use the pre-existing workspace-granted memory in a
workspace-bound chat; removing the persona from the workspace removes that
path immediately. A model never chooses or broadens grants.

Every pre-v3 memory is preserved with immutable `legacy_migrated` lineage as
`legacy_quarantined`, with `legacy_unknown` type/currentness and
`legacy_unresolved` provenance. New records receive immutable `native_v3`
lineage and cannot be relabeled into the legacy reset population. Migration
creates no access grant for legacy rows, even if an old mutable scope happens to
name a persona or workspace. They remain inspectable but read-only until a
future recovery flow can explicitly assign verified access and provenance.
They cannot enter a prompt; permanent deletion remains available.

## Lifecycle

- `pending`: extracted from an explicit user statement and awaiting review.
- `active`: approved or manually saved and eligible for retrieval.
- `rejected`: reviewed and intentionally not retained as context.
- `forgotten`: removed from future context without destroying its history.
- `superseded`: replaced by a newer edited revision.

New v3 memories also have one semantic type:

- `durable`: remains current until revised, marked stale, forgotten, or deleted;
- `temporal`: requires `valid_until` and stops qualifying after that timestamp;
- `stateful`: has `active`, `completed`, `cancelled`, or `superseded` state and
  qualifies as current context only while active.

Every new v3 record has `last_confirmed_at`. Retrieval also requires
`validity_status=current`. Phase 2 exposes these fields for explicit API
creation and revision; it does not infer temporal/state transitions or
automatically recognize conversational corrections.

Deliberate API memory saves start active and require at least one explicit
persona/workspace grant. The chat transcript action is different: it opens an
editable fact and creates a `pending` manual proposal with one source-persona
grant so raw assistant prose cannot silently become context. Editing an active
memory creates a new row linked through `supersedes_id`; it does not overwrite
the prior content or change its origin/access history. Approve, reject, forget,
and edit actions append audit events. Undo reverses the latest eligible action
when doing so does not violate ownership or exact-deduplication constraints.
Deleting a workspace or persona does not delete memory content or immutable
origin/grant history; v3 retrieval independently requires a current valid grant
target.

Forget and delete are deliberately different. Forget is reversible and retains
the row and audit history. Delete permanently removes the memory, all of its
history events, and its local FTS entry; it is unavailable to prompt retrieval
immediately and cannot be undone outside a backup.

## Provenance and extraction

Every new memory records its immutable origin, source message/turn when
applicable, confidence, extractor provider/model/version, last-confirmed time,
review timestamps, revision link, and access grant history. Legacy metadata is
retained, but unresolved origin is labeled rather than guessed.

After a successful assistant turn commits, a separate durable job asks the
configured memory-extraction Task Model to extract up to five stable user-stated
facts, preferences, relationships, identity details, or ongoing commitments.
The extractor may use a different model from the persona. It sees the
user statement as untrusted data. It is instructed to exclude secrets,
credentials, transient requests, assistant claims, guesses, and sensitive
medical/legal inferences. Invalid output fails only the extraction job; it cannot
change the already completed turn.

Task-model instruction is not the security boundary. Before any candidate is
persisted, the service discards content that redaction detects as a credential
or that explicitly claims to contain a password, passphrase, recovery code,
seed phrase, private key, API/client secret, or access/refresh/bearer token. The
same check runs again at the transaction boundary. Before calling the extraction
provider, and again after model work before writing anything, the service
revalidates the source user message, turn, chat binding, human principal,
persona, and workspace membership. Invalidated queued work completes without
sending the source text to the Task Model. Each accepted candidate is stored as
`pending`, durable/current, linked to the exact source, and granted only to the
source persona, including when the chat is workspace-bound. The extractor
cannot nominate a workspace or universal grant. The content-free extraction
job result reports only counts and safe task IDs.

Candidates never enter context automatically. Exact normalized duplicates of a
pending or active memory for the source persona are skipped. The candidate
limit is configured with `MEMORY_CANDIDATE_LIMIT`, clamped from one to ten.

## Retrieval

SQLite FTS5 provides lexical retrieval over memory content. Queries use a
bounded set of normalized non-stop-word terms. The repository first resolves
the durable chat binding and human principal, verifies the current persona and
workspace membership, and limits candidates to current, active records with an
unrevoked matching persona/workspace grant. FTS performs Boolean term matching,
then matching authorized rows use deterministic per-row recency ordering rather
than global-corpus BM25 statistics. Unauthorized persona or owner corpora
therefore cannot change authorized ordering. Personal chats do not receive
workspace-granted memory. Expired temporal memory and non-active stateful memory
are excluded.

FTS relevance is followed by recent authorized active memories to preserve
continuity when wording does not overlap; context budgeting performs a second
whole-entry selection pass. Legacy quarantined rows and pending, rejected,
forgotten, superseded, stale, expired, completed, or cancelled rows do not
enter prompt context.

FTS is deliberately lexical and local. A future semantic retriever may implement
the same binding/grant/lifecycle contract, but no embedding provider is implied
or advertised.

## Universal owner profile

`GET /api/v1/owner-profile` and `PUT /api/v1/owner-profile` expose an
allowlisted profile for deliberately universal basics: name, pronunciation,
pronouns, time zone, locale, preferred language, measurement units, and
communication/accessibility needs. Writes are explicit, normalize empty values
to null, reject credential-shaped content, increment a revision, and audit only
the names of changed fields.

The normal memory extractor cannot populate or modify this profile. Explicitly
populated values are rendered as a separate labeled universal-profile block for
every valid persona, even when ordinary saved memory is `off`; they are not
ranked, counted, or stored as memories. Phase 2 has no owner-profile browser
interface, onboarding population, or natural-language confirmation flow.

## API and browser behavior

Canonical memory APIs are under `/api/v1/memories`. They expose list/create,
revision, approve, reject, forget, delete, undo, history, and atomic explicit-ID
bulk-action contracts. `POST /api/v1/memory-proposals` creates an owner-scoped
pending proposal linked to an owned source message and derives the source
persona grant from that message's immutable chat binding. Manual creation
requires explicit grants. `PUT /api/v1/memories/{id}/grants` atomically replaces
the active persona/workspace grant set, while history retains grant/revocation
events. Undoing a content revision transfers the current revision's exact active
grant set to the restored revision in the same transaction; an old revision
cannot silently resurrect access that the owner revoked later. List responses
include typed record metadata, immutable origin, and active grants and can be
filtered by grant type/target.

These APIs are the Phase 2 control foundation, not the finished owner
experience. The typed browser uses the proposal contract for its message action
and retains existing review/history actions, but it does not yet provide the
full profile editor, searchable origin/access view, or grant-management
interface planned for Phase 3. The pre-Step-9 `/api/memory` adapters remain
removed.

The Memory settings view groups rows by lifecycle status, shows source type,
confidence, quarantine state, and active-grant count, and provides explicit
review/history actions for current records. Quarantined migrated rows are
read-only except for history inspection and permanent deletion. The view
can select all memories or a complete status group for bulk forget or permanent
delete. A forgotten or rejected row remains visible in History until explicitly
deleted or a broader retention policy is implemented. Phase 2 itself performs
no automatic activation, expiry deletion, live reset, or legacy-data deletion.
