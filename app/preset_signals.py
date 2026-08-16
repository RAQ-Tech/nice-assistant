"""What actually happened to the pictures a preset made.

Deliberately arithmetic. A preset earns a point when a picture it made is kept
or sent again, and loses one when a picture it made is removed. That is the
whole model, and it is the whole model on purpose: the number is meant to be
readable in the settings page next to the counts it came from, so that anyone
looking at it can say why it is what it is.

Generating a picture is not a signal. The platform chose the preset, so counting
that would be the platform scoring its own homework.

This never selects. It reorders presets that already passed every hard
requirement, and nothing here can make an incompatible preset eligible.
"""

from __future__ import annotations

from dataclasses import dataclass


KEPT = "kept"
SENT_AGAIN = "sent_again"
REMOVED = "removed"
SIGNAL_KINDS = (KEPT, SENT_AGAIN, REMOVED)


@dataclass(frozen=True)
class PresetSignals:
    preset_id: str
    kept: int = 0
    sent_again: int = 0
    removed: int = 0

    @property
    def weight(self) -> int:
        """Points earned minus points lost. Nothing cleverer."""

        return self.kept + self.sent_again - self.removed

    @property
    def total(self) -> int:
        """How much evidence there is. A weight of 1 from 1 signal is not the
        same as a weight of 1 from 20, and the settings page shows both."""

        return self.kept + self.sent_again + self.removed


def preferred_order(signals: list[PresetSignals]) -> list[str]:
    """Preset ids worth trying first, best evidence first.

    Only presets with a positive weight appear. A preset nobody has kept has
    nothing to say, and a preset whose pictures keep being removed should not be
    promoted by having been used a lot.
    """

    scored = [item for item in signals if item.weight > 0]
    scored.sort(key=lambda item: (-item.weight, -item.total, item.preset_id))
    return [item.preset_id for item in scored]


def describe(signals: PresetSignals) -> str:
    """A plain sentence for the settings page.

    Not "learned" or "preferred" - said in terms of what was counted, because
    that is all that happened.
    """

    if not signals.total:
        return "No pictures from this preset have been kept, sent again, or removed."
    parts = []
    if signals.kept:
        parts.append(f"{signals.kept} kept")
    if signals.sent_again:
        parts.append(f"{signals.sent_again} sent again")
    if signals.removed:
        parts.append(f"{signals.removed} removed")
    return ", ".join(parts)
