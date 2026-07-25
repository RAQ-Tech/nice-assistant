# Memory and Knowledge v3 plan

- Status: accepted
- Date: 2026-07-25
- Owners: Nice Assistant maintainers
- Related decisions: ADR 0026, ADR 0027, ADR 0028

## Purpose and status boundary

This document freezes the accepted Memory and Knowledge v3 product behavior and
delivery sequence. It is a requirements and acceptance plan, not a claim that
the described behavior is available.

Memory v2, as documented in `docs/memory.md`, ADR 0005, and ADR 0021, remains
the current behavior until the applicable v3 slices are implemented, tested,
migrated, and documented as shipped. In particular, the current system remains
review-first, stores one scope on each memory, and permits chat persona updates.
The README and current-behavior documents must not advertise v3 early.

No live memory content belongs in this document or anywhere else in the public
repository. Deployment-specific exports, backup identifiers, addresses, persona
content, and migration evidence remain outside Git.

The first implementation slice adds snapshot-only baseline export and
disposable reset-drill tooling. It does not change Memory v2 retrieval,
extraction, review, persona behavior, or chat binding. No production snapshot
has been exported by accepting this plan, no live baseline is claimed, and no
memory has been deleted.

## Intended product outcome

Memory and reference knowledge should make a persona feel continuous without
silently collapsing the owner's private contexts together. The target system
must:

- bind every chat permanently to one persona and one access context;
- keep learned memory private to the source persona unless the owner grants
  broader access;
- distinguish memory origin from current access;
- support deliberate persona and workspace sharing, including future workspace
  members and immediate revocation;
- learn a small, high-precision set of grounded memories automatically without
  making routine conversation into a review queue;
- preserve provenance, corrections, expiry, state transitions, and history;
- keep a deliberately populated universal owner profile separate from ordinary
  memory;
- treat uploaded documents as versioned reference knowledge rather than
  autobiographical memory; and
- expose optional fine control in Settings while keeping ordinary conversation
  natural.

## V1 boundaries and non-goals

V1 supports one human owner. Its identifiers and grant model must not assume
that only one human can ever exist, but multi-human accounts, collaboration, and
sharing interfaces are not part of v1.

V1 does not include:

- mid-chat persona or access-context changes;
- confidence-only activation, deletion, or expiry;
- automatic promotion into the universal owner profile;
- automatic conversion of document assertions into personal memory;
- per-persona extraction policies;
- semantic retrieval as a prerequisite;
- contradiction graphs beyond clear correction and supersession behavior;
- automatic expiry of pending candidates; or
- the future graphical “brain map” memory experience.

The first release uses one careful, system-wide automatic-memory policy. A
searchable Settings list, filters, provenance, access labels, and basic counts
are sufficient for v1.

## Target conceptual model

The names below describe responsibilities. Final table and class names may vary,
but implementation must preserve the boundaries.

### Human owner and universal profile

The authenticated account and the human whose facts are being represented are
separate concepts. V1 may enforce one human per owner account, but durable
records use a human identifier so a later multi-human model does not need to
reinterpret every memory as belonging to an ambiguous “user.”

The universal owner profile is not a memory scope. It contains only deliberately
shared basics such as:

- name and pronunciation;
- pronouns;
- time zone and locale;
- preferred language and measurement units; and
- communication or accessibility needs.

Onboarding and explicit, confirmation-gated instructions such as “All of my
personas should know this” may update the profile. The ordinary memory extractor
cannot write to it. Sensitive or situational information is never promoted
without an explicit universal-profile action.

### Immutable chat binding

At creation, a chat receives:

- one stable persona ID; and
- one access context: either personal or one workspace ID.

Those values are immutable. A persona rename changes display text, not identity.
A model/provider change and an Ollama or application restart do not change the
binding. A persona that belongs to multiple workspaces requires an explicit
context choice when the chat is created.

If the persona is archived, the workspace is archived, or the persona no longer
belongs to the bound workspace, the old chat remains readable to the owner but
cannot accept a new turn. The interface offers to start a new valid chat. It
must never silently rebind, copy, or continue the chat under another persona or
context. Legacy chats that cannot be mapped safely follow the same readable,
non-continuable behavior.

### Origin, grants, and authorization

A memory's origin records how it was learned, including the human owner, source
chat and persona, source message/turn or explicit action, extractor identity,
evidence, timestamps, and revision history. Origin is historical and does not
grant access by itself.

Access is represented separately as explicit grants to supported principal
types. V1 requires persona and workspace grants; the principal design reserves
future human grants without exposing unfinished multi-human behavior.

Automatically learned memory starts with exactly one persona grant for the
source persona, even when the chat is in a business workspace. Broader persona
or workspace grants require deliberate owner action. A workspace grant is
resolved against current workspace membership whenever knowledge is retrieved:
future personas added to the workspace inherit access, while removed personas
lose access immediately. Revocation changes authorization and does not delete
the memory, document, origin, or history.

A persona grant deliberately follows that stable persona across all of its
otherwise valid personal and workspace chat contexts. A workspace grant instead
requires the chat to be bound to that workspace and the persona to be a current
member. Settings must explain this difference before a grant is confirmed.

The owner may inspect and administer their records in Settings. That owner
management authority must not be confused with what a persona is allowed to
receive in a prompt.

Authorization precedes search, ranking, summarization, token budgeting, and
prompt construction. An inaccessible item must not enter a candidate result set,
score explanation, count, snippet, citation, or generated summary.

### Memory meaning and lifecycle

Memory has three meaning types:

- **durable**: remains current until corrected, forgotten, or deleted;
- **temporal**: has a `valid_until` value and stops being presented as current
  after that time; and
- **stateful**: represents a project, commitment, or other lifecycle item with
  states such as active, completed, cancelled, or superseded.

Administrative review status, truth/validity status, and stateful lifecycle are
separate concerns. The implementation must not overload one status field with
all three.

Every v3-admitted memory retains:

- creation and update timestamps;
- `last_confirmed_at`;
- source evidence and extractor provenance;
- memory type;
- `valid_until` when temporal;
- stateful lifecycle when applicable;
- review and access history; and
- correction or revision links.

`last_confirmed_at` is derived from the owner's supporting evidence even when a
record is pending or later becomes stale. A legacy row without trustworthy
confirmation evidence retains an explicit unknown value and cannot silently be
treated as current; migration never invents a confirmation date.

Expired, stale, completed, cancelled, superseded, forgotten, or rejected
records are excluded from current-fact prompting as appropriate, but remain
available as provenance/history until the owner explicitly deletes them.

A clear conversational correction may update memory automatically. It creates
or identifies the corrected current assertion and marks the prior assertion
stale or superseded without overwriting its content or history. It preserves
existing grants and cannot forget, delete, share, revoke, or change the owner
profile. Ambiguous contradictions remain pending rather than being resolved by
guesswork.

### Grounded automatic-memory policy

The extraction pipeline receives a bounded evidence packet, not an invitation
to invent a summary. It may include the current user message and only enough
causally prior conversation to resolve direct references or explicit acceptance
such as “Yes, let's do that.” Assistant text alone is never evidence of a user
fact; an accepted decision must have user evidence.

For each proposed assertion, the extractor returns constrained fields rather
than choosing access:

- normalized assertion;
- memory type and applicable temporal/state metadata;
- exact source message IDs and supporting spans;
- a bounded qualification reason code; and
- confidence as diagnostic metadata.

The platform validates ownership, evidence spans, grounding, allowed type,
credential restrictions, and schema before admission. Grounding uses the full
cited source context and must preserve speaker, subject, quotation, negation,
uncertainty, modality, time, and third-party attribution. Passwords,
passphrases, recovery codes, seed phrases, private keys, API/client secrets, and
access/refresh/bearer tokens are deterministically rejected even when the owner
has accepted the general sensitive-memory disclaimer.

The extractor does not grade its own work. Automatic qualification requires an
allowlisted extractive rule with deterministic transformation checks or a
separately versioned verifier that sees the full source and candidate. Extractor
confidence and rationale are not evidence. If independent entailment is
unavailable or ambiguous, the candidate cannot become active.

The possible outcomes are:

- **active** only for explicit, stable, grounded assertions that satisfy the
  measured automatic-activation policy, independent semantic qualification,
  and an allowlisted eligibility class;
- **pending** for plausible but materially ambiguous memory content; or
- **not admitted** when output is unsupported, non-memory, transient, malformed,
  duplicative, or credential-bearing.

Not admitting invalid extractor output is an input-validation decision, not
confidence-based deletion of a stored memory. Pending records do not expire in
v1. Confidence may help sort review and evaluate the policy, but it is never the
sole authority to activate, reject, delete, or expire a record. It may rank or
break ties among already qualified candidates; it cannot move an ambiguous
candidate from pending to active. Access expansion uses a separate unexecuted
action proposal and is never a pending-memory disposition.

Automatic activation remains disabled while the new pipeline runs in shadow
mode. Measured evidence must establish an acceptable high-precision policy
before activation is enabled system-wide. Numeric thresholds are selected from
evaluation results rather than guessed in this plan.

### Natural-language administration

Natural-language requests to remember, forget, share, revoke, or make something
universal may be detected in chat, but detection does not perform the action.
The browser presents a focused confirmation containing the exact proposed
content, affected record, access change, and consequence. Only explicit owner
confirmation calls the mutation API.

This confirmation is an exception triggered by an owner instruction. It does
not add routine memory reminders, review prompts, or memory-management chrome
to ordinary conversation.

Clear factual corrections are handled by the grounded correction policy, not
misclassified as access-administration requests. Mixed instructions are split:
the content correction may create or supersede ordinary memory, while every
forget, delete, share, revoke, or universal-profile effect remains an exact
unexecuted proposal until confirmation.

### Versioned document reference knowledge

Documents, memories, and the owner profile are different stores with different
qualification and retrieval rules.

A document uploaded through a chat initially grants access only to the persona
bound to that chat. A document uploaded through Settings requires at least one
explicit persona or workspace grant before ingestion. The owner can edit grants
later without deleting the document.

The system retains protected originals and immutable versions. Extraction,
classification, summaries, chunks, and citations identify the exact version
that produced them. A new version must not rewrite an old citation. Document
grants determine which versions may be retrieved; revocation prevents future
retrieval without falsifying historical provenance.

Document contents may be indexed, summarized, classified, and connected to
metadata. Their assertions do not become personal memory automatically. A fact
may become memory only after explicit promotion of the exact assertion or an
independent user-authored restatement. A short assent may establish one clearly
presented decision while discussing a document, but it cannot promote the
factual claims in a document or multi-claim summary. The document alone is
insufficient.

Document/version provenance follows derived assistant messages and durable
conversation summaries. Prompt assembly reauthorizes it on every turn. Revoked
document-derived content is excluded from model context; if it cannot be
separated safely, the chat remains owner-readable but cannot continue.
Superseded-version material remains labeled historical and cannot stand in for
fresh current-version retrieval.

Document-based answers display unobtrusive, claim-level citations by default.
A per-persona setting may hide their display, but internal provenance remains.
When visible, citations support authorized open/view/download actions and bind
to the exact version used.

Deleting a cited version removes its content and derived artifacts but retains
a minimal non-content “source unavailable” tombstone. Historical citations
never redirect to a newer version.

Supported formats, size/page limits, OCR behavior, and whether OCR is local to
the container or a verified LAN provider are deferred until the document phase.
Unavailable formats or OCR must be labeled honestly rather than accepted into a
nonfunctional ingestion path.

## Decision traceability

| # | Product decision | Future data/service contract | Future API and interface contract | Migration and verification |
|---|---|---|---|---|
| 1 | Chats are permanently bound to one persona and access context; rename is harmless. | Immutable persona ID plus personal/workspace context ID on every chat. | Creation requires both choices; update rejects rebinding; invalid chats are readable but cannot continue. | Classify legacy chats without guessing; prove rename, model switch, restart, rejected swap, same-chat continuity, and new-chat transcript isolation. |
| 2 | Learned memory defaults to source-persona-only, including in business workspaces. | Origin records chat/persona; platform creates one source-persona grant that deliberately follows that persona across valid contexts. | Extraction cannot submit a scope or broader grant; Settings explains persona-versus-workspace reach. | Prove no implicit workspace/global grant and no cross-persona retrieval. |
| 3 | Workspace removal revokes access without deleting material. | Workspace grants resolve current membership; grant/history records remain. | Membership change immediately affects retrieval and bound-chat validity. | Prove future-member inheritance, immediate removal, and unchanged underlying content. |
| 4 | A small explicit universal owner profile is separate from memory. | Human/profile records with an allowlisted field model and explicit provenance. | Onboarding and confirmed universal actions only; ordinary extraction has no write path. | Prove no auto-promotion and correct injection for every authorized persona. |
| 5 | V1 has one human, while the model anticipates more. | Human/principal IDs are distinct from persona/workspace and authentication concepts. | V1 exposes no unfinished collaborator UI. | Constraints enforce one human now; migration tests avoid hard-coded singleton ownership in knowledge rows. |
| 6 | Explicit sensitive facts use ordinary scoped rules for v1. | Same memory lifecycle and grants; deterministic credential exclusion remains. | Account acknowledgement and a concise Memories disclaimer explain the boundary. | Prove listed credentials never persist, other sensitive facts remain properly scoped, and disclosures do not weaken controls. |
| 7 | Shared history, decisions, projects, experiences, and commitments may qualify; casual suggestions do not. | Qualification reason codes distinguish accepted user facts/decisions from assistant proposals. | Settings exposes the reason and evidence when available. | Evaluation covers accepted decisions, unresolved commitments, assistant-only claims, and casual suggestions. |
| 8 | Durable, temporal, and stateful memory have different validity behavior. | Type, `valid_until`, evidence-derived `last_confirmed_at`, validity, and lifecycle metadata remain distinct; unknown legacy dates are not guessed. | Filters and detail views expose type and state; prompts exclude non-current claims. | Time-controlled tests prove expiry, completion, cancellation, staleness, and retained history. |
| 9 | Precision is favored over recall. | Hard admission and grounding gates precede disposition. | No interface claim that every useful fact will be remembered. | Shadow evaluation measures precision, unsupported assertion rate, and missed-memory samples. |
| 10 | Clear corrections update automatically without overwriting history. | New/current assertion plus stale/superseded links and events; grants/profile remain unchanged. | History shows the prior assertion and correction source. | Prove clear corrections, ambiguous contradictions, undo boundaries, non-current prompt exclusion, and no administrative side effects. |
| 11 | Natural-language memory/access instructions require confirmation. | Durable action proposal separate from the target mutation. | Focused chat pop-up shows exact action; mixed intents split correction from administrative effects; cancel is a no-op. | Prove detection cannot mutate, stale proposals fail safely, confirmation is authenticated/idempotent, and ordinary chats show no review reminders. |
| 12 | Pending does not expire; confidence cannot decide deletion. | Pending has no automatic retention deadline in v1; confidence remains diagnostic. | Search/rank pending; show a nonblocking sign-in warning above a later measured threshold. | Prove no time/confidence deletion and truthful warning counts. |
| 13 | Chat and Settings document uploads have different grant defaults. | Chat origin creates one persona grant; Settings requires explicit grants. | Upload UI names recipients and supports later edits. | Prove no implicit workspace grant, editable access, future workspace membership, and immediate revocation. |
| 14 | Documents are reference knowledge, not personal memory. | Separate document/version/chunk store; promotion requires an exact action, user-authored restatement, or one clearly accepted decision. | Document retrieval and memory promotion use separate actions. | Prove document assertions and generic assent never promote a source or multi-claim summary automatically. |
| 15 | Every document/upload keeps version history and exact citations. | Immutable versions, version-bound derived context/citations, and non-content deletion tombstones. | Version history is inspectable; citation targets never drift or redirect to latest. | Prove update, historical labeling, prompt-time reauthorization, deletion tombstones, backup/restore, and exact-version citation stability. |
| 16 | Document citations are visible by default and configurable per persona. | Citation provenance is always retained; persona stores display preference. | Claim-level citations offer authorized open/view/download; display can be disabled. | Prove multiple-source placement, preference persistence, hidden-display provenance, and revoked download denial. |
| 17 | Existing pending memory should be exported as a quality baseline before removal. | Read-only export includes every available field and marks unavailable rationale honestly. | Deliver through Codex from restricted storage outside the repository; choose retention/deletion afterward. | Verify counts, IDs, hashes, provenance, restrictive handling, and that no export enters Git or public output before deletion approval. |
| 18 | Existing memory may be reset, but persona personalities/instructions must remain. | Delete only enumerated memory IDs after a canonical export of complete persona definitions/dependencies and a keep-set review. | Reset requires verified exports, backup, target summary, and explicit destructive confirmation. | Compare full persona-definition data before/after, quarantine possible personality memories, test cascades on a restore, and prove chats/personas unchanged. |
| 19 | V1 Settings uses a searchable list, stats, and persona/workspace filters. | List responses resolve human-readable grant/origin names without weakening ID ownership. | Search, filters, counts, lifecycle/access edits, provenance, warning, and disclaimer live in Settings. | Browser/API tests cover filters, renamed entities, archived entities, counts, bulk safety, and narrow-screen behavior. |
| 20 | Begin with one system-wide automatic policy. | One versioned policy and extractor contract applies to all personas. | No per-persona quality controls are advertised in v1. | Shadow/live evaluation records the policy version; later per-persona work requires a separate decision. |

## Delivery phases

### Phase 0: design and acceptance freeze

- **Status: complete.**
- ADR 0026, ADR 0027, ADR 0028, and this plan are accepted as the target
  contracts without claiming that their target runtime behavior has shipped.
- The traceability rows and delivery phases define implementation-sized slices
  and their acceptance boundaries.
- Phase 1 adds a narrow synthetic Memory v2 extraction baseline. Expanding that
  corpus to cover v3 grounding, access, temporal/state, correction,
  natural-language action, and document boundaries—and selecting numeric
  shadow-mode promotion criteria—remain required before Phase 4 can enable
  automatic activation.

Exit condition: met. The contracts and deferred questions are explicit and
current-behavior documentation continues to describe Memory v2 truthfully.

### Phase 1: private baseline and reset safety

- **Status: in progress.** The repository provides the offline, snapshot-only
  exporter and disposable-only reset drill described below. Producing and
  reviewing the owner's private deployment baseline still requires an actual
  verified snapshot and has not been performed merely by implementing the
  tools.
- Obtain authenticated read-only access to the deployment or a verified backup.
- Create a private export of pending and active/history memory with every
  available field.
- Produce a readable summary and mark unavailable information as unavailable;
  do not infer a missing extraction rationale.
- Export and hash the complete canonical persona-definition records and all
  dependent instruction/configuration records rather than assuming a fixed
  field list.
- Inspect the memory export for possible persona-definition or instruction
  material and establish an explicit keep/quarantine set.
- Exercise the memory-only reset against a disposable backup copy.
- Record the existing extractor's behavior against a versioned synthetic,
  observe-only corpus without using confidence or scope as a semantic pass.

This phase's implemented tooling is intentionally incapable of accepting a
live database, applying a reset, or deleting data. The export freezes exact
legacy memory IDs, expands possible persona-definition candidates through their
revision relationships, and records an exact reset/quarantine partition for
review. The drill applies the proposed reset only to a temporary database
extracted from the supplied snapshot, then proves persona and protected
non-memory data remain unchanged.

Exit condition: the owner has received and verified the private baseline; the
reset targets are exact; persona preservation is proven. Live deletion still
requires a separate explicit confirmation.

### Phase 2: identity, binding, grants, and validity foundation

- Introduce human/principal, immutable chat-binding, owner-profile, origin,
  grant, validity, temporal, and stateful foundations through migration.
- Preserve existing data and classify ambiguous legacy records conservatively.
- Enforce authorization before retrieval and make invalid-bound chats
  non-continuable.
- Add owner-profile and named-grant service/API contracts without automatic
  extraction.

Exit condition: migration, verified-backup recovery, authorization, and
cross-persona isolation tests pass with current Memory v2 behavior either
deliberately adapted or compatibly isolated. In-place downgrade is refused when
it would remove access controls.

### Phase 3: owner controls, disclosure, and legacy isolation

- Add the universal owner profile interface.
- Add searchable memory lists, basic statistics, persona/workspace filters,
  readable access/origin labels, lifecycle detail, and grant editing.
- Add pending-depth sign-in warning with a measured threshold.
- Add onboarding acknowledgement and Memories disclaimer.
- Add confirmation-gated natural-language memory and access proposals without
  adding routine conversation reminders.
- Freeze the legacy reset/quarantine set to enumerated pre-v3 memory IDs.
- Before automatic activation, either perform the separately approved
  memory-only reset or quarantine all legacy rows so they cannot mix with v3
  current memory. Potential persona-definition material remains quarantined
  until the owner explicitly chooses how to preserve it.

Exit condition: the minimum control and disclosure surfaces are available,
legacy records cannot contaminate v3 current memory, and API, browser,
accessibility, and human-experience tests prove that fine control does not make
normal chat feel administrative.

### Phase 4: grounded extraction and lifecycle

- Implement the bounded evidence contract, full-context semantics checks, and
  independent qualification boundary.
- Add qualification reason codes, deterministic credential filtering, platform
  access assignment, correction history, and temporal/state transitions.
- Run the new policy in shadow mode without automatic activation; the currently
  shipped review-first behavior remains authoritative for any non-quarantined
  records during the transition.
- Review false positives, unsupported assertions, privacy failures, and
  ambiguous outcomes; tune the policy rather than relying on confidence alone.
- Enable automatic activation only after the accepted gate is met and Phase 3
  controls, disclosure, and legacy isolation remain verified.

Exit condition: the system-wide policy meets the measured quality and privacy
gate on deterministic and live-model evaluation.

### Phase 5: document foundation and citations

- Decide and document supported formats, limits, OCR/provider behavior, storage
  accounting, retention, and failure states.
- Implement protected originals, immutable versions, extraction, chunks,
  grants, and version-bound citations.
- Add chat and Settings upload flows with their distinct access defaults.
- Add authorized claim-level citation display and per-persona visibility
  preference.

Exit condition: parser/provider contracts, backups, restart recovery, grant
revocation, version history, and exact citations pass process, container, and
real-deployment acceptance.

### Phase 6: final migration, optional legacy reset, and release

- Create and verify a fresh backup and candidate migration drill.
- Re-run the private export immediately before any approved reset that was
  deferred from Phase 3.
- Require an explicit destructive confirmation that identifies memory targets
  by their frozen legacy IDs and confirms persona definitions and the keep set
  are excluded. Newly created v3 memories can never be swept into that reset.
- Run focused tests, the complete suite, relevant process/container smoke,
  public-repository privacy audit, and live deployment acceptance.
- Verify that no export, persona content, deployment identifier, or private
  topology entered Git history.

Exit condition: the deployed revision, migration result, data preservation,
privacy boundaries, and rollback evidence are verified.

## Private export and reset contract

The export/reset procedure is deliberately separate from ordinary deployment.
If authenticated data access or a verified backup is unavailable, the procedure
stops without creating a partial reconstruction or deleting anything.

The private export should include, when present:

- memory ID and content;
- administrative, validity, temporal, and stateful status;
- origin chat/persona/workspace IDs with separately resolved display names;
- source message/turn references;
- confidence, qualification reason, extractor provider/model/version, and exact
  evidence;
- creation, update, review, confirmation, expiry, forgetting, and supersession
  timestamps;
- grant and revocation history;
- correction/revision links; and
- audit events.

Fields absent from the current system are labeled unavailable. In particular,
the export must not invent a model rationale that was never stored.

The Phase 1 exporter accepts only a Nice Assistant snapshot ZIP:

```powershell
py -3 scripts/export_memory_baseline.py SNAPSHOT `
  --output-dir PRIVATE_DIRECTORY `
  [--owner-id OWNER_ID]
```

`PRIVATE_DIRECTORY` must resolve outside the repository. The command creates
unique private JSON and readable text artifacts and prints only a content-free
summary. It does not accept the configured live database, call a running Nice
Assistant instance, modify the snapshot, or delete anything. The JSON artifact
is the machine-readable source for the reset drill; the text artifact is for
private owner review.

The baseline freezes the exact memory-ID population observed in that snapshot.
Only records with immutable `legacy_migrated` lineage that remain marked
`legacy_quarantined` are reset-eligible; every `native_v3` memory is fixed in
the keep set and cannot be relabeled into the legacy pool. The possible-persona
keep/quarantine set for eligible legacy rows is conservative and deterministic.
Any memory related through supersession, origin revision, or memory-event
revision links is included in the same protected revision closure so a later
reset cannot sever retained provenance through a foreign-key `SET NULL`.
Eligible legacy rows linked to native v3 memory are quarantined rather than
deleted. These sets are evidence for review, not permission to mutate a live
database.

Run the reset proof only against the same snapshot and its private baseline:

```powershell
py -3 scripts/drill_memory_reset.py SNAPSHOT BASELINE_JSON
```

The exporter and drill each copy the complete source ZIP into a tool-owned
temporary directory and inspect only that immutable copy, with capacity checks
for the ZIP, extracted database, and safety headroom. They verify the original
path again before reporting success or publishing artifacts. The drill then
extracts a disposable database, verifies the snapshot and baseline binding,
simulates deletion of only the frozen reset IDs inside that temporary copy, and
compares persona, chat, protected non-memory, foreign-key, integrity, and FTS
evidence. It prints only content-free verification and discards the temporary
copies. It has no live-database, output, apply, or deletion mode.

Persona inventory includes raw core persona records, workspace links, behavioral
configuration, visual-identity metadata, and dependent instruction/configuration
digests. A metadata-only snapshot contains those database records but not the
identity-reference image bytes. The exporter must label those artifacts
`not_included_by_metadata_only_snapshot`; it must not report them as missing or
claim byte-level preservation. A full snapshot is required to compare the
stored reference digest with the included file bytes.

The artifact is written to a user-selected private path outside the repository
and delivered through the private Codex session. An ignored repository path is
not considered sufficient protection merely because Git omits it. The export
applies and verifies restrictive mode bits where POSIX permissions are
available. On Windows it must report that inherited ACLs remain unverified and
require operator review rather than claiming `chmod` established owner-only
access. Artifact content must not be printed through terminal or application
logs and must never appear in fixtures, screenshots, issues, commits, or pull
requests. After delivery, the owner makes an explicit retain-or-delete choice
for the export. Verified backups remain residual copies until their separately
documented retention or deletion policy removes them.

Before a live reset:

1. create and verify a recoverable database backup;
2. verify export counts and stable hashes;
3. export and hash complete persona definitions plus every dependent
   instruction/configuration record;
4. inspect memory content for possible persona-definition material and establish
   an explicit keep or quarantine set;
5. enumerate exact memory IDs and their event/search-index dependencies, then
   verify foreign-key and cascade effects against a disposable restore;
6. show the owner a destructive-action summary and obtain confirmation;
7. delete only the enumerated, confirmed memory IDs rather than issuing a broad
   table wipe;
8. verify canonical persona-definition data and chat records are unchanged; and
9. verify memory retrieval, Settings counts, restart recovery, and backup
   restoration.

## Evaluation and rollout gates

### Privacy gate

- No inaccessible memory or document may affect candidate retrieval, rank,
  snippets, summaries, counts, citations, or generated output.
- Source-persona-only defaults, future workspace-member inheritance, immediate
  removal, invalid chat blocking, and owner administration are tested
  independently.
- Passwords, passphrases, recovery codes, seed phrases, private keys, API/client
  secrets, and access/refresh/bearer tokens have zero accepted persistence in
  the deterministic evaluation corpus. Other explicitly stated sensitive
  personal facts continue through ordinary scoped-memory rules.

Any privacy failure blocks release and automatic activation.

### Grounding and quality gate

- Every automatically admitted assertion maps to owned source messages and
  exact supporting spans and passes a separately versioned full-context
  entailment boundary or an allowlisted deterministic extractive rule.
- Grounding preserves speaker, subject, quotation, negation, uncertainty,
  modality, time, and third-party attribution. An extractor cannot satisfy this
  gate with its own confidence or rationale.
- Assistant-only claims, questions, negated claims, hypothetical statements,
  temporary requests, and unaccepted suggestions do not become active memory.
- Explicit stable facts, preferences, accepted decisions, important shared
  experiences, projects, and unresolved commitments are evaluated separately.
- Generic assent cannot promote a document or multi-claim summary; a document
  fact requires exact promotion or an independent user-authored restatement,
  while short assent can establish only one clearly presented decision.
- Precision is the primary enablement metric. Recall is measured for visibility
  but does not justify lowering the privacy or grounding boundary.
- Pending contains plausible ambiguity rather than malformed or unsupported
  extractor output.
- Automatic activation requires an allowlisted eligibility class and
  independent semantic qualification regardless of confidence.

The evaluation report records corpus version, policy version, provider, model,
and result categories. Live-model evaluation supplements deterministic tests;
it does not replace them.

### Lifecycle gate

- Temporal facts stop appearing as current after `valid_until`.
- Stateful items transition among active, completed, cancelled, and superseded
  without rewriting history.
- Clear corrections replace current prompt truth while preserving the old
  assertion, evidence, and grants; administrative or owner-profile effects
  remain confirmation-gated.
- Every v3-admitted memory has evidence-derived `last_confirmed_at`; legacy
  records without trustworthy evidence remain explicitly unknown. Stale and
  expired records remain inspectable but are not phrased as current facts.
- Pending does not expire or disappear because of confidence.

### Resilience gate

- Model/provider changes do not alter chat binding or stored knowledge.
- Completed data survives Ollama and application restarts with persistent
  storage.
- A new chat receives authorized profile/memory/document knowledge without
  inheriting another chat's transcript or summary; same-chat causal history
  remains available only inside its unchanged valid binding.
- Document-derived messages and summaries retain version/grant provenance and
  are reauthorized on every prompt. Revoked inseparable context blocks
  continuation rather than leaking.
- Interrupted extraction or document processing is visible and recoverable and
  cannot corrupt a completed chat turn.
- Migration and restore drills preserve origins, grants, versions, citations,
  complete persona-definition dependencies, and owner-profile boundaries.

### Experience gate

- Normal chat contains no unsolicited memory review interface.
- Explicit natural-language administration produces one understandable,
  cancelable confirmation.
- Settings can search and filter by real persona/workspace names and inspect
  access, provenance, state, and history.
- Document citations are unobtrusive, claim-level, accessible, and useful when
  enabled.
- Unavailable ingestion, OCR, citation, or model capabilities are labeled
  honestly.

## Documentation transition

ADR 0026, ADR 0027, ADR 0028, and this plan are accepted. Their target runtime
behavior remains unshipped, so existing documents continue to describe Memory
v2 truthfully. The offline Phase 1 tooling is documented separately and must
not be interpreted as a v3 runtime cutover.

As each implementation slice ships, the same change updates the applicable:

- `docs/memory.md`;
- `docs/conversation-context.md`;
- `docs/task-models.md`;
- `docs/architecture.md`;
- `docs/security-model.md`;
- `docs/settings-experience.md`;
- `docs/testing.md`;
- `docs/operations.md`;
- `docs/deployment-acceptance.md`;
- `docs/roadmap.md`;
- `docs/debt-register.md`; and
- historical-plan superseding notes where needed.

The README changes only after user-visible behavior is implemented and verified.
ADR 0005 and ADR 0021 remain accepted records of the current and historical
design; accepted v3 ADRs will state precisely which parts they supersede.

## Deferred decisions

The following are intentionally deferred and do not block the identity/access
foundation:

- document formats, file/page limits, OCR requirements, and OCR provider;
- the measured pending-count warning threshold;
- the numeric shadow-evaluation threshold and review sample size;
- retention periods for stale memory history, uncited document versions,
  citation tombstones, private exports, and residual backups;
- multi-human account and collaboration interfaces;
- per-persona automatic-memory policies;
- semantic retrieval;
- generalized contradiction resolution; and
- graphical brain-map memory exploration.

Each deferred capability requires an explicit decision and truthful readiness
contract before implementation.
