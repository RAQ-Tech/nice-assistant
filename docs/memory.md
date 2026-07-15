# Memory v2 policy

Nice Assistant memory is review-first. Conversation extraction creates pending
candidates; only active memories can enter prompts or media continuity context.
`off` disables both retrieval and candidate extraction for that chat.

## Lifecycle

- `pending`: extracted from an explicit user statement and awaiting review.
- `active`: approved or manually saved and eligible for retrieval.
- `rejected`: reviewed and intentionally not retained as context.
- `forgotten`: removed from future context without destroying its history.
- `superseded`: replaced by a newer edited revision.

Manual saves start active. Editing creates a new row linked through
`supersedes_id`; it does not overwrite the prior content. Approve, reject,
forget, and edit actions append audit events. Undo reverses the latest eligible
action when doing so does not violate scope ownership or exact-deduplication
constraints. Deleting a workspace or persona archives its live memories instead
of hard-deleting them.

Forget and delete are deliberately different. Forget is reversible and retains
the row and audit history. Delete permanently removes the memory, all of its
history events, and its local FTS entry; it is unavailable to prompt retrieval
immediately and cannot be undone outside a backup.

## Provenance and extraction

Every memory records its source type, source message/turn when applicable,
confidence, extractor provider/model/version, review timestamps, and revision
link. Legacy rows migrate with `legacy` provenance; their origin is not guessed.

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
same check runs again at the transaction boundary. The content-free extraction
job result reports only how many sensitive candidates were filtered.

Candidates never enter context automatically. Exact normalized duplicates of a
pending or active memory in the same scope are skipped. The candidate limit is
configured with `MEMORY_CANDIDATE_LIMIT`, clamped from one to ten.

## Retrieval

SQLite FTS5 provides lexical retrieval over memory content. Queries use a
bounded set of normalized non-stop-word terms. Retrieval is always owner scoped,
then limited to global plus the current workspace, persona, and chat. Only
`active` rows qualify. FTS relevance is followed by recent active memories to
preserve continuity when wording does not overlap; context budgeting performs a
second whole-entry selection pass.

FTS is deliberately lexical and local. A future semantic retriever may implement
the same owner/scope/status contract, but no embedding provider is implied or
advertised by Memory v2.

## API and browser behavior

Canonical memory APIs are under `/api/v1/memories`. They expose list/create,
revision, approve, reject, forget, delete, undo, history, and atomic explicit-ID
bulk-action contracts. The typed browser uses these contracts directly; the
pre-Step-9 `/api/memory` adapters are removed.

The Memory settings view groups rows by real lifecycle status and scope, shows
provenance and confidence, and provides explicit review/history actions. It can
select all memories or a complete status group for bulk forget or permanent
delete. A forgotten or rejected row remains visible in History until explicitly
deleted or a broader retention policy is implemented.
