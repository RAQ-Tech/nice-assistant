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


# The shipped values, named once.
#
# These were duplicated in `AppConfig`, which meant changing the default context
# window required changing two numbers that had no reference to each other -
# and missing one produced a deployment that disagreed with itself about how
# much room a persona had. A value somebody can change in Settings should exist
# in exactly one place in the code.
DEFAULT_CONTEXT_WINDOW_TOKENS = 8192
DEFAULT_OUTPUT_TOKENS = 512
DEFAULT_SUMMARY_TRIGGER_RATIO = 0.75
DEFAULT_MAX_COMPACTION_PASSES = 2
# Headroom kept back so an estimate that runs slightly under does not overrun
# the window. Written three separate ways before this existed - twice as
# math.ceil and once as integer arithmetic - which is how three copies of one
# rule quietly stop agreeing.
SAFETY_RESERVE_RATIO = 0.05
SAFETY_RESERVE_MINIMUM_TOKENS = 256


def safety_reserve_tokens(context_window_tokens: int) -> int:
    """How much of a window is held back rather than offered to the prompt."""

    return max(SAFETY_RESERVE_MINIMUM_TOKENS, math.ceil(int(context_window_tokens) * SAFETY_RESERVE_RATIO))


def prompt_budget_tokens(context_window_tokens: int, output_tokens: int) -> int:
    """What is left for instructions, persona, memory and conversation."""

    return int(context_window_tokens) - int(output_tokens) - safety_reserve_tokens(context_window_tokens)


@dataclass(frozen=True)
class ContextPolicy:
    default_context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    summary_trigger_ratio: float = DEFAULT_SUMMARY_TRIGGER_RATIO
    max_compaction_passes: int = DEFAULT_MAX_COMPACTION_PASSES
    output_tokens_default: int = DEFAULT_OUTPUT_TOKENS
    memory_ratio: float = 0.15
    summary_ratio: float = 0.20
    card_max_ratio: float = 0.30
    owner_profile_max_ratio: float = 0.10
    example_ratio: float = 0.10
    lore_ratio: float = 0.12
    history_floor_ratio: float = 0.25
    recent_messages_to_preserve: int = 8
