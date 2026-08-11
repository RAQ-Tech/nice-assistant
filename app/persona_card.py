"""Persona character card rendering and its save-time context budget.

The card is authored character material that is always present in a turn. It is carried
in the protected prompt section, which fails a turn rather than degrading quietly, so the
size limit is enforced when the card is saved rather than when a turn is planned.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from app.context_policy import ContextPolicy, TokenEstimator


CARD_FIELDS = ("card_definition", "card_personality", "card_style", "card_behavior")

CARD_LABELS = {
    "card_definition": "Character definition (facts about who this persona is)",
    "card_personality": "Character personality (disposition, values, flaws, fears)",
    "card_style": "Character style (how this persona speaks)",
    "card_behavior": "Character behavior (how this persona acts)",
}

MINIMUM_CONTEXT_WINDOW_TOKENS = 2048


@dataclass(frozen=True)
class CardBudget:
    """The window the cap is derived from, and the resulting allowance."""

    context_window_tokens: int
    prompt_budget_tokens: int
    cap_tokens: int


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
    )


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
