"""Context budget primitives.

These are kept free of service imports so prompt-assembly helpers can share the same
estimator and ratios without depending on the service that consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math


class TokenEstimator:
    """Conservative provider-neutral estimate used before providers report usage."""

    @staticmethod
    def text(text: str) -> int:
        return max(1, math.ceil(len((text or "").encode("utf-8")) / 3))

    def message(self, message: dict) -> int:
        structured = ""
        if message.get("tool_calls"):
            structured += json.dumps(message["tool_calls"], separators=(",", ":"), ensure_ascii=False)
        if message.get("tool_name"):
            structured += str(message["tool_name"])
        return 6 + self.text((message.get("content") or "") + structured)

    def messages(self, messages: list[dict]) -> int:
        return 3 + sum(self.message(message) for message in messages)


@dataclass(frozen=True)
class ContextPolicy:
    default_context_window_tokens: int = 4096
    summary_trigger_ratio: float = 0.75
    max_compaction_passes: int = 2
    output_tokens_default: int = 512
    memory_ratio: float = 0.15
    summary_ratio: float = 0.20
    card_max_ratio: float = 0.30
    recent_messages_to_preserve: int = 8
