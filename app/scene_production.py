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

    def __init__(
        self,
        backlog,
        logger,
        *,
        interval_seconds: int = DEFAULT_POLL_SECONDS,
        enabled: bool = False,
        memories=None,
        resources=None,
    ):
        self.backlog = backlog
        self.logger = logger
        # Vectors for new memories are computed here rather than when a memory
        # is written: approving a fact should not wait for a model, and a model
        # that is down should not stop somebody approving it.
        self.memories = memories
        self.resources = resources
        self.interval_seconds = max(30, int(interval_seconds))
        self.enabled = bool(enabled)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Two kinds of background work share this thread. Producing approved
        # scenes needs pre-generation switched on; keeping memory vectors
        # current does not, and tying it to a picture setting would mean recall
        # quietly depending on something unrelated.
        if self._thread or not (self.backlog and self.enabled) and not self.memories:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="background-work", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread:
            thread.join(timeout=5)

    def _embed_pending(self) -> None:
        """Catch memory vectors up, without letting that stop production."""

        if not self.memories:
            return
        try:
            self.memories.embed_pending()
        except Exception:  # noqa: BLE001 - a missing model must not stop pictures
            self.logger.warning("memory embedding pass failed", exc_info=True)

    def _snapshot_avatars(self) -> None:
        """Adopt avatars the product does not own copies of yet.

        Same posture as the embedding pass: it must never stop production, and
        an avatar site being down is a wait, not an error.
        """

        if not self.resources:
            return
        try:
            self.resources.snapshot_pending_avatars()
        except Exception:  # noqa: BLE001 - a broken avatar must not stop pictures
            self.logger.warning("avatar snapshot pass failed", exc_info=True)

    def run_once(self) -> list[dict]:
        """One pass over every owner with something approved.

        Returns a result per owner, including the ones that started nothing, so
        a quiet night can be told apart from a broken one.
        """

        self._embed_pending()
        self._snapshot_avatars()
        results = []
        if not (self.backlog and self.enabled):
            return results
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
