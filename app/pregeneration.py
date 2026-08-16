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

from app.service_errors import RequestError


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


PREFERENCE_KEYS = (
    "pregeneration_enabled",
    "pregeneration_start_hour",
    "pregeneration_end_hour",
    "pregeneration_max_per_run",
)


def validate_preferences(preferences: dict) -> None:
    """Refuse a window that could never fire, when it is saved.

    A start hour equal to its end hour matches no hour at all. Storing it would
    produce a switch that is on, a schedule that looks set, and a feature that
    never runs, which is the worst of the three.
    """

    start = preferences.get("pregeneration_start_hour")
    end = preferences.get("pregeneration_end_hour")
    for key in ("pregeneration_start_hour", "pregeneration_end_hour"):
        if key in preferences and not _valid_hour(preferences[key]):
            raise RequestError(f"{key} must be a whole number of hours from 0 to 23", 422)
    if start is not None and end is not None and _valid_hour(start) and _valid_hour(end) and int(start) == int(end):
        raise RequestError(
            "The quiet window start and end cannot be the same hour; that window never matches.",
            422,
        )
    limit = preferences.get("pregeneration_max_per_run")
    if limit is not None and not (isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 20):
        raise RequestError("pregeneration_max_per_run must be a whole number from 1 to 20", 422)


def _valid_hour(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23


def policy_for_owner(preferences: dict, deployment: "PregenerationPolicy") -> "PregenerationPolicy":
    """The policy actually in force for one owner.

    The stored setting decides the window, the cap, and whether it is on. The
    deployment keeps one veto: if it has switched pre-generation off, no browser
    can switch it back on. This feature runs the GPU unattended, and the machine
    it runs on has overheated before.
    """

    values = preferences if isinstance(preferences, dict) else {}
    enabled = values.get("pregeneration_enabled")
    return PregenerationPolicy(
        enabled=bool(deployment.enabled and (deployment.enabled if enabled is None else enabled)),
        start_hour=_hour(values.get("pregeneration_start_hour"), deployment.start_hour),
        end_hour=_hour(values.get("pregeneration_end_hour"), deployment.end_hour),
        max_per_run=(
            int(values["pregeneration_max_per_run"])
            if _valid_limit(values.get("pregeneration_max_per_run"))
            else deployment.max_per_run
        ),
    )


def _hour(value, fallback: int) -> int:
    return int(value) if _valid_hour(value) else int(fallback)


def _valid_limit(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 20


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
