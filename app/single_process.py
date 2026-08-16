"""The deployment runs as one process, and that is load-bearing.

Three things live in memory rather than in the database: turn event replay,
login throttling, and request metrics. A second process would have its own copy
of all three, so a browser reconnecting could be answered by a process that
never saw the turn, and a login lockout would be worth as many attempts as there
are processes.

That is a deliberate design for one private-LAN application process, recorded in
ADR 0034. This module is the part that stops the assumption being violated by
accident: the usual ways of asking for more than one worker are refused at
startup with the reason, rather than starting and behaving strangely.

It is a guard, not a proof. Someone determined to run several processes can, by
launching them directly. It catches the configuration change somebody makes
without knowing this constraint exists, which is the case worth catching.
"""

from __future__ import annotations


# The environment variables a process manager conventionally reads to decide how
# many workers to run.
WORKER_VARIABLES = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS", "NICE_ASSISTANT_WORKERS")

REASON = (
    "Nice Assistant runs as one process. Turn event replay, login throttling, and metrics are held in "
    "memory, so a second process would answer reconnects it never saw and multiply the login lockout. "
    "See ADR 0034. Set {variable} to 1, or remove it."
)


def multi_process_reason(environment) -> str:
    """Why this process must not start, or an empty string.

    A value that is not a number is left alone. Refusing to start over something
    unparseable would be this module deciding it understands a process manager
    it has never seen.
    """

    for variable in WORKER_VARIABLES:
        raw = str((environment or {}).get(variable) or "").strip()
        if not raw:
            continue
        try:
            workers = int(raw)
        except ValueError:
            continue
        if workers > 1:
            return REASON.format(variable=variable)
    return ""


def require_single_process(environment) -> None:
    """Refuse to start when the environment asks for more than one worker."""

    refusal = multi_process_reason(environment)
    if refusal:
        raise RuntimeError(refusal)
