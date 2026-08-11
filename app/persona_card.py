"""Persona character card rendering and its save-time context budget.

The card is authored character material that is always present in a turn. It is carried
in the protected prompt section, which fails a turn rather than degrading quietly, so the
size limit is enforced when the card is saved rather than when a turn is planned.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from app.context_policy import ContextPolicy, TokenEstimator


# The four capped fields. Example dialogue is stored alongside them but budgeted separately:
# it is droppable data, so it is clipped at turn time instead of rejected at save time.
CARD_FIELDS = ("card_definition", "card_personality", "card_style", "card_behavior")
CARD_STORED_FIELDS = (*CARD_FIELDS, "card_example_dialogue")

CARD_LABELS = {
    "card_definition": "Character definition (facts about who this persona is)",
    "card_personality": "Character personality (disposition, values, flaws, fears)",
    "card_style": "Character style (how this persona speaks)",
    "card_behavior": "Character behavior (how this persona acts)",
}

MINIMUM_CONTEXT_WINDOW_TOKENS = 2048

EXAMPLE_BLOCK_DELIMITER = "<START>"
EXAMPLE_DIALOGUE_LABEL = (
    "[Persona voice examples: illustrate how this persona speaks, not conversation history]"
)
EXAMPLE_USER_PLACEHOLDER = "{{user}}"
EXAMPLE_CHAR_PLACEHOLDER = "{{char}}"
# The account holder is addressed generically. A persona speaks to whoever is in the chat,
# and the stored username is an account credential rather than a chosen display name.
EXAMPLE_USER_NAME = "User"


def example_dialogue_blocks(raw: str | None) -> list[str]:
    """Split stored example dialogue into whole exchanges on the delimiter line."""

    blocks = []
    current: list[str] = []
    for line in str(raw or "").splitlines():
        if line.strip() == EXAMPLE_BLOCK_DELIMITER:
            if any(item.strip() for item in current):
                blocks.append("\n".join(current).strip())
            current = []
            continue
        current.append(line)
    if any(item.strip() for item in current):
        blocks.append("\n".join(current).strip())
    return blocks


def render_example_block(block: str, persona_name: str) -> str:
    return block.replace(EXAMPLE_CHAR_PLACEHOLDER, persona_name or "Assistant").replace(
        EXAMPLE_USER_PLACEHOLDER, EXAMPLE_USER_NAME
    )


def selected_example_blocks(raw: str | None, persona_name: str, budget_tokens: int, estimator) -> list[str]:
    """Whole exchanges up to the budget. A half exchange teaches nothing, so a block is
    included entire or not at all, and later ones are dropped first."""

    selected: list[str] = []
    for block in example_dialogue_blocks(raw):
        candidate = [*selected, render_example_block(block, persona_name)]
        if estimator.text("\n\n".join(candidate)) > budget_tokens:
            break
        selected = candidate
    return selected


def select_example_dialogue(raw: str | None, persona_name: str, budget_tokens: int, estimator) -> str:
    return "\n\n".join(selected_example_blocks(raw, persona_name, budget_tokens, estimator))


@dataclass(frozen=True)
class CardBudget:
    """The window the cap is derived from, and the resulting allowances."""

    context_window_tokens: int
    prompt_budget_tokens: int
    cap_tokens: int
    example_tokens: int
    history_floor_tokens: int


def card_values(source) -> dict[str, str]:
    """Normalize the four card fields from a row, mapping, or partial payload."""

    getter = source.get if hasattr(source, "get") else lambda field, default=None: getattr(source, field, default)
    values = {}
    for field in CARD_FIELDS:
        value = getter(field, None)
        values[field] = str(value or "").strip()
    return values


def render_card_block(source) -> str:
    """Render the populated card fields as labelled lines, in a fixed order."""

    values = card_values(source)
    return "\n".join(f"{CARD_LABELS[field]}: {values[field]}" for field in CARD_FIELDS if values[field])


def card_token_estimate(source) -> int:
    """Cost of the card exactly as it enters the prompt. Empty cards cost nothing."""

    rendered = render_card_block(source)
    return TokenEstimator.text(rendered) if rendered else 0


def _prompt_budget_tokens(context_window_tokens: int, policy: ContextPolicy) -> int:
    """The same output and safety reserves ContextService.plan() subtracts."""

    output_tokens = min(max(1, policy.output_tokens_default), max(1, context_window_tokens // 2))
    safety_tokens = max(256, math.ceil(context_window_tokens * 0.05))
    return context_window_tokens - output_tokens - safety_tokens


def _integer_setting(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def smallest_configured_context_window(preferences: dict | None, policy: ContextPolicy) -> int:
    """The narrowest window a turn using this persona can resolve to.

    A persona is not bound to one model, so the cap is taken against the smallest window
    the account has configured rather than the most generous one. Per-request overrides
    are not visible here; those remain a turn-time concern.
    """

    preferences = preferences if isinstance(preferences, dict) else {}
    candidates = [
        _integer_setting(
            preferences.get("models_context_window_tokens") or policy.default_context_window_tokens,
            policy.default_context_window_tokens,
        )
    ]
    overrides = preferences.get("model_overrides")
    if isinstance(overrides, dict):
        for override in overrides.values():
            if isinstance(override, dict) and override.get("context_window_tokens"):
                candidates.append(
                    _integer_setting(override["context_window_tokens"], policy.default_context_window_tokens)
                )
    return max(MINIMUM_CONTEXT_WINDOW_TOKENS, min(candidates))


def card_budget(preferences: dict | None = None, policy: ContextPolicy | None = None) -> CardBudget:
    policy = policy or ContextPolicy()
    window = smallest_configured_context_window(preferences, policy)
    prompt_budget = _prompt_budget_tokens(window, policy)
    return CardBudget(
        context_window_tokens=window,
        prompt_budget_tokens=prompt_budget,
        cap_tokens=max(1, int(prompt_budget * policy.card_max_ratio)),
        example_tokens=max(1, int(prompt_budget * policy.example_ratio)),
        history_floor_tokens=max(0, int(prompt_budget * policy.history_floor_ratio)),
    )


def example_dialogue_fit(raw: str | None, persona_name: str, budget: CardBudget) -> tuple[int, int, int]:
    """How many stored exchanges are authored, how many fit today, and what they cost."""

    blocks = example_dialogue_blocks(raw)
    if not blocks:
        return 0, 0, 0
    selected = selected_example_blocks(raw, persona_name, budget.example_tokens, TokenEstimator())
    rendered = "\n\n".join(selected)
    return len(blocks), len(selected), TokenEstimator.text(rendered) if rendered else 0


def cap_percent(budget: CardBudget) -> int:
    """The cap as a percentage of the prompt budget, for messages that must name the budget."""

    if budget.prompt_budget_tokens <= 0:
        return 0
    return int(round(budget.cap_tokens / budget.prompt_budget_tokens * 100))


def card_too_large_message(estimate: int, budget: CardBudget) -> str:
    return (
        f"This character card is {estimate} tokens and the limit is {budget.cap_tokens}. "
        f"The limit is {cap_percent(budget)} percent of the {budget.prompt_budget_tokens}-token prompt "
        f"budget for a {budget.context_window_tokens}-token context window. Shorten the card, or raise "
        "the model context allocation in Settings."
    )
