"""The loop that makes approved scenes while nobody is using the machine.

Deliberately thin. Every decision about whether a picture may start lives in
`app.pregeneration` and every decision about what to make lives in the scene
backlog; this only decides when to ask. Keeping it that way means the hard part
stays testable without threads or clocks.

The loop is off unless background production is switched on, so a deployment
that never enables it never starts a thread it does not use.
"""

from __future__ import annotations

import threading


DEFAULT_POLL_SECONDS = 300


class SceneProductionRunner:
    """Asks the scene backlog to produce, on an interval, until stopped."""

    def __init__(self, backlog, logger, *, interval_seconds: int = DEFAULT_POLL_SECONDS, enabled: bool = False):
        self.backlog = backlog
        self.logger = logger
        self.interval_seconds = max(30, int(interval_seconds))
        self.enabled = bool(enabled)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or not self.backlog or self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scene-production", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread:
            thread.join(timeout=5)

    def run_once(self) -> list[dict]:
        """One pass over every owner with something approved.

        Returns a result per owner, including the ones that started nothing, so
        a quiet night can be told apart from a broken one.
        """

        results = []
        for user_id in self._owners():
            try:
                outcome = self.backlog.produce_due(user_id)
            except Exception:
                # One owner's failure must not stop production for the rest, and
                # must not take the loop down with it.
                if self.logger:
                    self.logger.warning("background picture production failed for one owner")
                continue
            results.append({"user_id": user_id, **outcome})
        return results

    def _owners(self) -> list[str]:
        try:
            return self.backlog.owners_with_work()
        except Exception:
            if self.logger:
                self.logger.warning("background picture production could not read the backlog")
            return []

    def _loop(self) -> None:
        # Wait first: a restart is the least likely moment for the machine to be
        # idle, and the recovery sweep has only just returned entries to the
        # queue.
        while not self._stop.wait(self.interval_seconds):
            self.run_once()
