from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal

from app.provider_contracts import ChatToolCall
from app.service_errors import RequestError


CAPABILITY_TERMINAL_STATES = {"completed", "failed", "cancelled", "denied", "expired"}
CAPABILITY_LEGAL_TRANSITIONS = {
    "pending_confirmation": {"queued", "denied", "cancelled", "expired"},
    "queued": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "denied": set(),
    "expired": set(),
}


@dataclass(frozen=True)
class MediaTaskRequirements:
    """Semantic media request. Resource identity is deliberately excluded."""

    kind: Literal["image", "video"]
    prompt: str
    operation: Literal["generate", "inpaint", "outpaint", "image_to_image"] = "generate"
    domains: tuple[str, ...] = ()
    content_tags: tuple[str, ...] = ()
    required_features: tuple[str, ...] = ()

    def as_arguments(self) -> dict:
        return {
            "prompt": self.prompt,
            "operation": self.operation,
            "domains": list(self.domains),
            "content_tags": list(self.content_tags),
            "required_features": list(self.required_features),
        }


@dataclass(frozen=True)
class CapabilityDefinition:
    key: str
    tool_name: str
    title: str
    description: str
    kind: Literal["image", "video"]
    permission_mode: Literal["confirm", "explicit"] = "confirm"

    def tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "A detailed semantic description of the requested media.",
                            "minLength": 1,
                            "maxLength": 100000,
                        }
                    },
                },
            },
        }

    def public(self) -> dict:
        return {
            "key": self.key,
            "tool_name": self.tool_name,
            "title": self.title,
            "description": self.description,
            "permission_mode": self.permission_mode,
            "available": True,
        }


class CapabilityRegistry:
    def __init__(self, definitions: list[CapabilityDefinition] | None = None):
        definitions = definitions or [
            CapabilityDefinition(
                key="media.generate_image",
                tool_name="generate_image",
                title="Generate image",
                description=(
                    "Request an image from the platform. Supply visual intent only; the platform applies its "
                    "configured provider and settings. The user must approve before generation starts."
                ),
                kind="image",
            ),
            CapabilityDefinition(
                key="media.generate_video",
                tool_name="generate_video",
                title="Generate video",
                description=(
                    "Request a video from the platform. Supply visual intent only; the platform applies its "
                    "configured provider and settings. The user must approve before generation starts."
                ),
                kind="video",
            ),
            CapabilityDefinition(
                key="media.edit_image",
                tool_name="edit_image",
                title="Edit image",
                description=(
                    "Edit an owner-selected image with a configured ComfyUI workflow. This capability is explicit-only "
                    "until the task model can resolve protected media attachments safely."
                ),
                kind="image",
                permission_mode="explicit",
            ),
        ]
        self._by_key = {item.key: item for item in definitions}
        self._by_tool = {item.tool_name: item for item in definitions}
        if len(self._by_key) != len(definitions) or len(self._by_tool) != len(definitions):
            raise ValueError("capability definitions must have unique keys and tool names")

    def definitions(self) -> list[CapabilityDefinition]:
        return list(self._by_key.values())

    def tools(self) -> list[dict]:
        return [item.tool_schema() for item in self.definitions()]

    def by_key(self, key: str) -> CapabilityDefinition:
        try:
            return self._by_key[key]
        except KeyError as exc:
            raise RequestError("unsupported capability", 400) from exc

    def by_kind(self, kind: str) -> CapabilityDefinition:
        for item in self.definitions():
            if item.kind == kind:
                return item
        raise RequestError("unsupported media kind", 400)

    def from_tool_call(self, call: ChatToolCall) -> tuple[CapabilityDefinition, MediaTaskRequirements]:
        definition = self._by_tool.get(call.name)
        if not definition:
            raise RequestError("The model requested an unsupported capability.", 400)
        return definition, self.requirements(definition, call.arguments)

    @staticmethod
    def requirements(definition: CapabilityDefinition, arguments: dict) -> MediaTaskRequirements:
        if not isinstance(arguments, dict):
            raise RequestError("Capability arguments must be an object.", 400)
        unknown = set(arguments) - {"prompt"}
        if unknown:
            raise RequestError("Capability arguments included unsupported fields.", 400)
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            raise RequestError("Capability prompt required.", 400)
        if len(prompt) > 100_000:
            raise RequestError("Capability prompt is too long.", 400)
        return MediaTaskRequirements(kind=definition.kind, prompt=prompt)


def capability_tool_result(capability: dict) -> str:
    """Return the minimal safe result sent back into future model context."""

    result = capability.get("result") if isinstance(capability.get("result"), dict) else {}
    payload = {
        "capability": capability.get("capability_key"),
        "status": capability.get("status"),
    }
    if result.get("mediaId"):
        payload["media_id"] = result["mediaId"]
    if capability.get("error"):
        payload["error"] = capability["error"]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
