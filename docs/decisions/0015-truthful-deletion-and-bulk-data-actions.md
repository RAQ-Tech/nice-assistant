# ADR 0015: Truthful deletion and bulk data actions

- Status: accepted
- Date: 2026-07-15
- Owners: Nice Assistant maintainers

## Context

The canonical memory `DELETE` route performed a reversible forget transition,
while the canonical chat `DELETE` route only hid the chat. Those behaviors were
safe but not truthful. They also forced users with substantial history to act on
every chat or memory individually.

## Decision

Reversible and destructive actions use distinct contracts. Memory forget remains
an audited, undoable lifecycle transition; memory delete permanently removes the
memory row, its audit events, and its FTS entry. Chat hide removes a chat from the
ordinary list; chat delete permanently removes the chat, messages, turns, and
summaries while retaining independently owned artifacts and completed job/audit
records according to their foreign-key policies.

Owner-scoped bulk endpoints accept explicit ID sets and execute atomically. The
browser provides selection, select-all/group controls, exact affected counts,
and a separate destructive confirmation. Active chat work blocks permanent chat
deletion rather than racing a provider callback.

## Alternatives considered

- Keep `DELETE` as a soft action and add a second purge route. Rejected because
  the canonical verb would remain misleading.
- Automatically purge forgotten memory after a fixed interval. Rejected because
  retention policy is deployment-specific and forget is intentionally undoable.
- Perform bulk changes as repeated browser requests. Rejected because partial
  failure would leave an unclear result and be unnecessarily slow.

## Consequences

Clients that previously used memory `DELETE` to forget must use the explicit
`/forget` action. The current typed browser is migrated in the same change.
Permanent deletion cannot be undone outside a backup. Bulk operations do not
weaken ownership checks and never select records implicitly on the server.

## Verification

Backend tests cover forget versus delete, memory-event and FTS removal, atomic
owner isolation, chat hide versus delete, and cascade behavior. Browser tests
cover canonical routes, select-all controls, exact confirmations, and bulk
refresh behavior.
