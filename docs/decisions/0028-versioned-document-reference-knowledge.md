# ADR 0028: Versioned document reference knowledge

- Status: proposed
- Date: 2026-07-25
- Owners: Nice Assistant maintainers

## Context

Nice Assistant needs to use proposals, invoices, design plans, and other uploaded
documents without treating their contents as autobiographical memory. Documents
may contain sensitive material, change over time, and support only particular
claims. Their access, version, and provenance therefore need to remain explicit
through ingestion, retrieval, presentation, backup, and deletion.

Memory scope is not a sufficient document boundary. A document can be shared
with several personas or workspaces, a workspace grant must apply to future
members, and a newer upload must not make citations to an older version
ambiguous. Model-selected relevance must never expand access.

This ADR defines the intended product and architecture contract. It does not
claim that document upload, parsing, retrieval, or citations are currently
implemented. Supported v1 formats, size and page limits, and the real OCR
boundary remain phase-design decisions that must be documented and verified
before the feature is advertised.

## Decision

Documents are a separate reference-knowledge subsystem. The platform stores an
owner-scoped logical document, immutable uploaded versions, extracted passages,
and version-bound provenance. Indexing, text extraction, OCR, summarization, and
classification may derive searchable metadata or passages, but these derived
records remain reference knowledge tied to their exact source version.

Access uses deterministic persona and workspace grants:

- A document uploaded through a chat initially grants access only to that
  chat's permanently bound persona.
- A document uploaded through Settings requires the user to choose at least one
  persona or workspace grant during upload.
- The user may add or remove persona and workspace grants later without copying
  the document.
- Workspace grants are evaluated from current membership, so future workspace
  personas gain access and removed personas lose access immediately.
- Authorization filters candidate documents, versions, and passages before any
  lexical or semantic retrieval, ranking, summarization, or model prompt.

Uploading a replacement creates a new immutable version of the same logical
document through an explicit versioning action. It does not overwrite prior
bytes, passages, metadata, or citations. Retrieval uses the current version by
default, while an explicit historical request may select an older version.
Every citation records the exact document and version used.

Document assertions never automatically become personal memory. A fact may
enter memory only when the user explicitly promotes the exact assertion or
independently restates it in the user's own words under the memory policy. A
short assent may establish one clearly presented decision while discussing a
document, but it cannot promote every claim in a document, assistant summary,
or multi-claim answer. Mere appearance in the document is insufficient
evidence.

Visible document citations are enabled by default and configurable per persona.
When enabled, citations are unobtrusive, attached to the claims they support,
and allow the user to open, view, or download the authorized source version.
When citation display is disabled, the platform still retains internal
version-bound provenance and must not imply that an uncited statement came from
personal memory. Multiple supporting documents receive citations on the
specific claims each one supports.

Originals, versions, extracted text, passages, summaries, classifications,
indexes, previews, and citation metadata are owner-scoped sensitive artifacts.
Download and preview endpoints enforce the same grants as retrieval. Backups
must either include all required document artifacts and version metadata or
truthfully identify themselves as metadata-only. Deleting a grant revokes access
without deleting the document. Deleting a document or version is a distinct,
explicitly confirmed operation with clearly documented effects on history,
citations, indexes, backups, and recoverability.

Document/version provenance also follows derived assistant messages and durable
conversation summaries. Prompt assembly reauthorizes that provenance on every
turn. Content derived from a document that is no longer authorized is excluded
from model context; if it cannot be separated safely from a message or summary,
the chat remains owner-readable but cannot continue. A superseded version
remains labeled historical and cannot be reused as the current document without
an explicit historical request or fresh current-version retrieval.

Physical deletion of a cited version removes its bytes and derived content but
leaves a minimal, non-content tombstone so historical citations resolve to
“source unavailable.” A tombstone never redirects a citation to a newer
version. The document-phase retention design must specify which metadata the
tombstone keeps and prove that it cannot disclose removed content.

The v1 phase design must select and verify concrete file formats, upload/page
limits, parser behavior, malformed-file handling, and OCR implementation. Nice
Assistant will not advertise scanned-document support until a real OCR path is
implemented and accepted. External parsers or OCR services, if used, remain
separate provider boundaries with documented privacy, timeout, failure, and
retention behavior.

## Alternatives considered

- Store document text as ordinary memories. Rejected because it would conflate
  reference assertions with facts about the user, lose version provenance, and
  risk cross-persona disclosure.
- Copy a document for every persona or workspace. Rejected because copies would
  drift, complicate deletion, and make citations and version history ambiguous.
- Let the retrieval or language model decide access. Rejected because relevance
  is probabilistic and cannot be an authorization boundary.
- Replace old files in place. Rejected because answers could no longer prove
  which content supported a claim.
- Hide or discard provenance when visible citations are disabled. Rejected
  because a presentation preference must not weaken auditability or source
  truth.
- Choose formats and OCR behavior in this ADR. Rejected because support must be
  based on implemented parsers, operational limits, and real acceptance
  evidence rather than an architectural promise.

## Consequences

Document storage requires additional protected artifact, version, grant,
passage, and citation records plus indexes that can be rebuilt without changing
source identity. Access changes must invalidate caches and derived retrieval
sets promptly. Persona deletion, workspace membership changes, backup restore,
and document deletion need explicit referential and audit behavior.

Immutable versions increase storage use, and exact-version citations require
retaining older versions until the user deliberately deletes them under the
document retention policy. Backup size and restore time will grow. Operators
need storage reporting, safe limits, parser/OCR readiness, and a documented
recovery procedure.

The browser needs separate chat-upload and Settings-management flows, recognizable
persona/workspace names, grant editing, version history, source preview/download,
and per-persona citation-display settings. These controls remain supporting UI
and must not turn ordinary conversation into a document administration console.

This decision establishes a shared grant boundary that memory and documents may
reuse while keeping their content models and promotion rules separate. Future
multi-human collaboration may extend the grantee model, but v1 authorizes one
human owner's personas and workspaces only.

## Verification

- Migration tests preserve existing chats, personas, workspaces, memories, and
  artifacts while adding document, version, grant, passage, and citation data.
- Authorization tests cover persona-only chat uploads, mandatory Settings
  grants, multiple grants, future workspace-persona inheritance, immediate
  removal revocation, owner isolation, and denial before retrieval and ranking.
- Version tests prove uploads are immutable, current-version retrieval is the
  default, historical retrieval is explicit, and citations always resolve to
  the exact bytes and version used.
- Conversation-context tests prove document/version provenance survives into
  derived messages and summaries, is reauthorized on every prompt build, and
  cannot be reused after grant revocation; inseparable context blocks
  continuation rather than leaking.
- Memory tests prove indexing, summaries, classifications, and document
  assertions do not create personal memories. Explicit promotion, independent
  user-authored restatement, and one clearly accepted decision use the normal
  memory policy and retain their conversational evidence; generic assent never
  promotes a multi-claim source or summary.
- Browser tests cover explicit upload access, later grant editing, version
  history, default-on per-persona citation display, claim-level citations,
  authorized open/view/download, and retained provenance when display is off.
- Security tests reject unauthorized list, search, preview, download, citation,
  and stale-cache access, including after workspace membership removal.
- Parser tests enforce selected file, byte, and page limits; reject malformed or
  unsupported content safely; and prove OCR is unavailable unless a real,
  readiness-checked implementation is configured.
- Backup and restore tests prove whether originals and every cited version are
  included, restore grant and citation integrity, rebuild derived indexes, and
  truthfully label metadata-only backups.
- Deletion tests distinguish grant revocation from document/version deletion,
  remove derived indexes consistently, leave non-content “source unavailable”
  tombstones for cited deleted versions without redirecting them, and report
  backup recoverability honestly.
- Focused tests, the complete suite, process smoke, container smoke, public-repo
  privacy audit, and a real deployment acceptance pass must succeed before the
  capability is documented as shipped.
