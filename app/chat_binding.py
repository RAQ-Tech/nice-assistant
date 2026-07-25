"""Canonical, dynamically validated chat identity and access bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.service_errors import ConflictError


_BLOCK_MESSAGES = {
    "legacy_binding_unresolved": (
        "This older chat does not have a verified persona and access context. "
        "Its history is still available, but start a new chat to continue."
    ),
    "human_principal_unavailable": (
        "This chat's owner profile is unavailable. Its history is still available, but it cannot be continued."
    ),
    "persona_unavailable": (
        "This chat's persona is no longer available. Its history is still available, but start a new chat to continue."
    ),
    "workspace_unavailable": (
        "This chat's workspace is no longer available. Its history is still available, "
        "but start a new chat to continue."
    ),
    "persona_not_in_workspace": (
        "This persona is no longer available in this chat's workspace. "
        "The existing history is still readable, but start a new chat to continue."
    ),
    "invalid_binding": (
        "This chat's persona and access context could not be validated. "
        "Its history is still available, but it cannot be continued."
    ),
}


@dataclass(frozen=True)
class ChatBindingResolution:
    human_id: str | None
    persona_id: str | None
    persona_name: str | None
    binding_status: str
    context_kind: str | None
    workspace_id: str | None
    workspace_name: str | None
    can_continue: bool
    block_code: str | None = None
    block_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "human_id": self.human_id,
            "persona_id": self.persona_id,
            "persona_name": self.persona_name,
            "binding_status": self.binding_status,
            "context": {
                "kind": self.context_kind,
                "workspace_id": self.workspace_id,
                "workspace_name": self.workspace_name,
            },
            "can_continue": self.can_continue,
            "block_code": self.block_code,
            "block_message": self.block_message,
        }


def resolve_chat_binding(repo, user_id: str, chat) -> ChatBindingResolution:
    """Resolve a chat's immutable IDs and current continuation authorization."""

    binding = repo.chat_binding(chat.id)
    if not binding:
        return _blocked(
            binding_status="legacy_unresolved",
            block_code="legacy_binding_unresolved",
            persona_id=getattr(chat, "persona_id", None),
            workspace_id=getattr(chat, "workspace_id", None),
            context_kind="workspace" if getattr(chat, "workspace_id", None) else None,
        )

    human_id = _text(getattr(binding, "human_id", None))
    persona_id = _text(getattr(binding, "persona_id", None))
    workspace_id = _text(getattr(binding, "workspace_id", None))
    context_kind = _text(getattr(binding, "context_kind", None))
    binding_status = _text(getattr(binding, "binding_status", None)) or "legacy_unresolved"
    persona_snapshot = _text(getattr(binding, "persona_name_snapshot", None))
    workspace_snapshot = _text(getattr(binding, "workspace_name_snapshot", None))

    if binding_status != "active":
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_snapshot,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_snapshot,
            block_code=("legacy_binding_unresolved" if binding_status == "legacy_unresolved" else "invalid_binding"),
        )

    human = repo.human_principal(user_id)
    if not human or _text(getattr(human, "id", None)) != human_id:
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_snapshot,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_snapshot,
            block_code="human_principal_unavailable",
        )

    persona = repo.persona(user_id, persona_id) if persona_id else None
    persona_name = _text(getattr(persona, "name", None)) or persona_snapshot
    if not persona:
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_name,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_snapshot,
            block_code="persona_unavailable",
        )

    if context_kind == "personal" and workspace_id is None:
        return ChatBindingResolution(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_name,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=None,
            workspace_name=None,
            can_continue=True,
        )

    if context_kind != "workspace" or not workspace_id:
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_name,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_snapshot,
            block_code="invalid_binding",
        )

    workspace = repo.workspace(user_id, workspace_id)
    workspace_name = _text(getattr(workspace, "name", None)) or workspace_snapshot
    if not workspace:
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_name,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            block_code="workspace_unavailable",
        )
    if workspace_id not in repo.persona_workspace_ids(persona_id):
        return _blocked(
            human_id=human_id,
            persona_id=persona_id,
            persona_name=persona_name,
            binding_status=binding_status,
            context_kind=context_kind,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            block_code="persona_not_in_workspace",
        )
    return ChatBindingResolution(
        human_id=human_id,
        persona_id=persona_id,
        persona_name=persona_name,
        binding_status=binding_status,
        context_kind=context_kind,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        can_continue=True,
    )


def require_continuable_chat(repo, user_id: str, chat) -> ChatBindingResolution:
    resolution = resolve_chat_binding(repo, user_id, chat)
    if not resolution.can_continue:
        raise ConflictError(resolution.block_message or _BLOCK_MESSAGES["invalid_binding"])
    return resolution


def _blocked(
    *,
    binding_status: str,
    block_code: str,
    human_id: str | None = None,
    persona_id: str | None = None,
    persona_name: str | None = None,
    context_kind: str | None = None,
    workspace_id: str | None = None,
    workspace_name: str | None = None,
) -> ChatBindingResolution:
    return ChatBindingResolution(
        human_id=human_id,
        persona_id=persona_id,
        persona_name=persona_name,
        binding_status=binding_status,
        context_kind=context_kind,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        can_continue=False,
        block_code=block_code,
        block_message=_BLOCK_MESSAGES[block_code],
    )


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "ChatBindingResolution",
    "require_continuable_chat",
    "resolve_chat_binding",
]
