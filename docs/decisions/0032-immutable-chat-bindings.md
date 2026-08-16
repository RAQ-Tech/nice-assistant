# ADR 0032: A chat is bound to its workspace and persona at creation

- Status: accepted
- Date: 2026-08-16
- Owners: Nice Assistant maintainers

## Context

A chat could be retargeted after it already had a transcript. Two paths did it:
`PUT /api/v1/chats/{id}` wrote a new `persona_id` after checking only that the
persona belonged to the account, and `POST /api/v1/chats/{id}/turns` accepted
`workspace_id` and `persona_id` in its payload and wrote both back onto the chat
before generating.

Neither is a theoretical hole. Retargeting leaves the previous persona's
assistant replies in the transcript, and the transcript is what the next model
prompt is built from, so one persona's words entered another persona's context.
The update path also accepted a persona from a different workspace, which the
turn path then rejected with `persona not found` - so a chat could be saved into
a state where it could no longer be used.

The membership rule existed correctly in `create_chat` and was reimplemented,
differently, in `create_turn`. Two copies of one rule is how they drift.

## Decision

- Chat creation is the only place a `workspace_id` and `persona_id` are decided.
  Nothing later rewrites either field.
- One implementation of the rule: `Repository.resolve_chat_binding` validates
  the pair and returns what to bind. `create_chat` calls it. Nothing else needs
  its own version, because nothing else may bind.
- A turn payload may still carry `workspace_id` and `persona_id` for
  compatibility. Values equal to the chat's binding are accepted and ignored;
  different values are refused with `409` before the user message, turn, job, or
  chat row is written, so a refused request changes nothing.
- `PUT /api/v1/chats/{id}` no longer retargets. Title, model override, memory
  mode, and hidden state remain editable; a differing `persona_id` or
  `workspace_id` is refused with the same `409`.
- The browser stops sending both fields on a turn. The persona picker already
  lived only in the new-chat dialog, so choosing a different persona already
  created a new chat; this removes the redundant fields rather than adding a
  new flow.
- Migration `0031` repairs rows the old behaviour produced. The persona is kept
  and the workspace corrected to one that persona belongs to, because the
  transcript was written by that persona and keeping it is what keeps every
  reply attributable. A chat with no persona is left alone. Nothing is deleted
  and no message is reattributed.

## Consequences

- Moving a conversation to another persona is not possible, by design. Copying a
  transcript into a new binding is a fork, which is a separate feature with its
  own questions about what should carry over; it is not this.
- `workspace_id` and `persona_id` on `POST /chats/{id}/turns` are deprecated.
  They remain accepted while they match. Removing them is a future breaking
  change and is not scheduled here.
- `create_turn` gets slightly smaller, which helps the separate work to bring it
  under the complexity ceiling, but that is a side effect and not the reason.

## Alternatives considered

- Allow retargeting and rewrite or hide the earlier transcript. Rejected: it
  either destroys the record or silently reattributes somebody's words.
- Allow retargeting only for chats with no messages yet. Rejected as a rule that
  is invisible until it fires; a control that works until the conversation
  starts is worse than one that never appears.
- Validate in the service layer instead of the repository. Rejected because the
  check needs the same database lookups either way, and putting it beside the
  only writer keeps the rule and its enforcement in one place.
