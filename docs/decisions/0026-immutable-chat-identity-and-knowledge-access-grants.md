# ADR 0026: Immutable chat identity and knowledge-access grants

- Status: accepted; Phase 2 identity/access foundation implemented
- Date: 2026-07-25
- Owners: Nice Assistant maintainers

## Context

Memory v2 stores one scope on each memory. That representation cannot preserve
where knowledge came from while independently sharing it with several personas
or workspaces. It also cannot express a workspace grant as a continuing
relationship: copying access to today's workspace members would omit personas
added later and could leave access behind after a persona is removed.

Chat identity and access context are similarly security-sensitive. Changing a
chat's persona or workspace after messages exist would let the same history be
interpreted under a different identity and authorization boundary. Names are
mutable display values and therefore cannot safely serve as either binding.

Nice Assistant also needs a deliberately universal set of owner basics without
turning ordinary memories into globally available facts. The first release has
one human owner, but hard-coding that singleton into ownership and access rows
would make later multi-human support a destructive redesign.

The Phase 2 identity/access foundation is implemented: new chats require an
immutable persona/context binding, memory retrieval is grant-scoped, the
explicit universal owner profile is separate from memory, and legacy rows are
preserved but quarantined. The complete Settings grant/profile control surface
and natural-language confirmation workflow remain later phases.

## Decision

### Immutable chat identity

- At creation, every chat is bound to an immutable persona ID and exactly one
  immutable access context: either the owner's personal context or one workspace
  ID. A persona or workspace name is display metadata and a rename does not
  alter the binding.
- A chat cannot switch personas or access contexts. Continuing with another
  persona or workspace requires a new chat.
- Chat creation validates that the persona may participate in the selected
  context. Authorization is reevaluated before every new turn.
- If later membership, archival, or deletion makes the stored binding invalid,
  the existing chat and messages remain readable to their authorized owner, but
  new turns are blocked with an explicit reason and a path to start a valid new
  chat. The system never silently rebinds or repairs the chat.

### Knowledge origin and grants

- Every typed memory record has immutable lineage: `legacy_migrated` for rows
  carried forward by the v3 migration and `native_v3` for records created by the
  new system. Only `legacy_migrated` records that remain
  `legacy_quarantined` can enter a legacy reset plan. Native lineage cannot be
  changed or reshaped into legacy quarantine.
- A memory's immutable origin records its human owner, source chat, source
  persona, source workspace context when applicable, and source turn/message
  evidence. Origin explains provenance; it does not itself grant access.
- Access is represented independently as one or more explicit grants. Version
  one supports persona grants and workspace grants. An ordinary memory has no
  implicit global scope.
- An automatically learned memory receives a source-persona grant by default,
  including when its source chat is in a workspace. It is not automatically
  shared with that workspace or any other persona.
- A deliberate owner action may add or remove grants for specific personas or
  workspaces without changing or deleting the underlying memory or provenance.
- A persona grant follows that stable persona across every otherwise valid
  personal or workspace chat context. This is the deliberate meaning of
  source-persona-only access; Settings must make the cross-context effect
  explicit. A workspace grant is narrower in the other dimension: it authorizes
  a chat only when that chat is bound to the granted workspace and its bound
  persona is a current member.
- Workspace grants are evaluated against current membership rather than copied
  into per-persona grants. A persona added later gains access to pre-existing
  workspace-granted knowledge. Removing it from the workspace revokes that
  route immediately without deleting the knowledge. A separate persona grant,
  if present, remains independently effective.
- Every search, prompt-context query, count, preview, and future ranking stage
  applies ownership and grant authorization first. Unauthorized records never
  enter lexical, semantic, or model-based ranking and are not exposed through
  result metadata.

### Universal owner profile and future principals

- Deliberately shared owner basics live in a small structured owner profile,
  separate from ordinary memories and their retrieval index. Eligible fields
  include name, pronunciation, pronouns, time zone, locale, preferred language,
  measurement units, and communication or accessibility needs.
- Only onboarding or an explicit, confirmed owner action may populate or change
  the owner profile. The normal memory extractor cannot create, promote, or
  modify owner-profile values. Sensitive or situational information is not made
  universal without that deliberate action.
- Version one exposes one human owner, while chats, origins, profiles, grants,
  and audit records use stable human-principal identifiers so a future release
  can add humans without reinterpreting singleton-owned data.
- Passwords, API keys, access tokens, private keys, recovery codes, and
  equivalent authentication material are prohibited from ordinary memory and
  the owner profile, even when explicitly stated. Extraction and mutation
  boundaries must reject or redact them rather than relying on access grants.

This decision defines identity and authorization, not automatic-memory quality
or activation policy. When implemented and shipped, it supersedes only the
single-scope access assumptions in ADR 0005 and the access-selection assumptions
of the chat memory proposal in ADR 0021. Their review, audit, critical-path, and
truthful-lifecycle decisions remain in force unless a separate accepted ADR
changes them.

## Alternatives considered

- Keep one `global`, `workspace`, `persona`, or `chat` scope per memory.
  Rejected because one field conflates provenance with authorization and cannot
  express several grants safely.
- Duplicate a memory for each authorized persona. Rejected because edits,
  corrections, provenance, and deletion would diverge across copies.
- Expand a workspace grant into the workspace's current persona IDs. Rejected
  because future members would miss earlier knowledge and removed members could
  retain it unless every membership change performed a flawless rewrite.
- Allow persona or workspace switching inside an existing chat. Rejected
  because prior history, summaries, and source evidence would cross an identity
  boundary that was not in effect when they were created.
- Treat selected ordinary memories as global or let extraction populate a
  universal profile. Rejected because accidental promotion would expose
  situational or sensitive facts to every persona.
- Retrieve and rank broadly, then filter unauthorized results. Rejected because
  ranking, logging, caches, counts, and model inputs would become disclosure
  paths even if the final response omitted a record.

## Consequences

Chats gain stable, rename-safe identity and cannot silently change privacy
contexts. Memory provenance survives sharing and revocation, while one record
can be shared with several personas and workspaces without duplication.
Workspace membership changes take effect immediately, including for knowledge
created before the membership change.

The persistence layer needs stable human-principal ownership, immutable chat
bindings, immutable origins, many-to-many grants, structured owner-profile
records, and auditable grant changes. APIs and Settings must resolve IDs to
current display names, make origin distinct from current access, and explain
that a persona grant follows the persona across valid contexts while a workspace
grant follows current workspace membership. Legacy chats and memories require a
deterministic, nondestructive forward migration; ambiguous records must be
quarantined rather than silently broadened. Because removing
binding/quarantine/grant metadata could reopen access, recovery to a pre-v3
release uses the verified pre-migration backup instead of an in-place downgrade.
Existing global memories must not be converted automatically into the owner
profile.

Authorization queries become more complex and must remain part of the database
query boundary before retrieval or ranking. Membership changes can alter access
without changing memory rows, so caches must either include the relevant access
revision or be invalidated synchronously.

The owner profile provides useful universal basics without creating a general
global-memory channel. Supporting future human principals adds schema and API
discipline now even though version one still presents a single-owner product.

## Verification

- Migration tests preserve persona definitions, chats, messages, memory text,
  provenance, and lifecycle history; ambiguous legacy access never broadens
  silently, native lineage cannot be relabeled into the legacy reset pool, and
  legacy global records never auto-populate the owner profile.
- Chat tests prove creation requires a valid persona/context pair, IDs survive
  renames, mid-chat persona and context changes are rejected, and an invalidated
  chat remains readable while new turns are blocked.
- Authorization matrix tests cover personal and workspace chats, independent
  persona and workspace grants, multiple simultaneous grants, and owner
  isolation.
- Membership tests prove a new workspace persona can use pre-existing
  workspace-granted knowledge, removal revokes that route immediately, and an
  independent persona grant remains effective.
- Retrieval tests prove unauthorized records are excluded before FTS, semantic
  search, scoring, counts, previews, caching, prompt assembly, and model calls.
- Continuity tests prove that a new chat for the same authorized persona/context
  receives eligible owner-profile and granted knowledge without inheriting the
  prior chat's transcript or summary, while a different persona or context is
  denied unless its own grants authorize the record.
- Owner-profile tests prove only onboarding and explicit confirmed mutations
  can write eligible fields; extraction cannot promote values or write the
  profile.
- Secret-handling tests prove credential-shaped content is rejected or redacted
  by extraction and direct mutation paths and never enters memory, profile,
  retrieval indexes, logs, or prompts as stored knowledge.
- API, browser, process, and deployment-acceptance tests prove current
  persona/workspace access labels, immediate revocation, honest blocked-chat
  behavior, and zero cross-persona or cross-workspace disclosure.
