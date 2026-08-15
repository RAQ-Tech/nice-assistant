"""When it is acceptable to make pictures nobody has asked for yet.

Pre-generation spends real electricity on a machine somebody else is using, so
the decision to start is kept small, pure, and testable on its own. Everything
here answers one question: may a background picture start right now, and if not,
why not.

The answer is always accompanied by a reason. "Nothing happened last night" is
otherwise indistinguishable from "it is broken", and an operator cannot act on
the difference without being told which it was.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PregenerationPolicy:
    """Deployment-level limits on background picture production."""

    enabled: bool = False
    start_hour: int = 2
    end_hour: int = 6
    max_per_run: int = 3

    def window_contains(self, hour: int) -> bool:
        """Is this hour inside the quiet window?

        The window may wrap past midnight, which is the normal case for one
        expressed in quiet hours.
        """

        start = self.start_hour % 24
        end = self.end_hour % 24
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end


@dataclass(frozen=True)
class PregenerationDecision:
    allowed: bool
    reason: str


def may_produce(
    policy: PregenerationPolicy,
    *,
    hour: int,
    interactive_pending: int,
    media_pending: int,
    media_active: int,
    approved_waiting: int,
) -> PregenerationDecision:
    """Decide whether one background picture may start now."""

    if not policy.enabled:
        return PregenerationDecision(False, "background picture production is switched off")
    if not policy.window_contains(hour):
        return PregenerationDecision(
            False,
            f"outside the {policy.start_hour:02d}:00-{policy.end_hour:02d}:00 quiet window",
        )
    if interactive_pending:
        # Someone is talking to the assistant right now. Their turn matters more
        # than a picture nobody has asked for.
        return PregenerationDecision(False, "a conversation is waiting")
    if media_pending or media_active:
        return PregenerationDecision(False, "a requested picture is already queued or running")
    if not approved_waiting:
        return PregenerationDecision(False, "no approved scene is waiting")
    return PregenerationDecision(True, "quiet, idle, and a scene is approved")
