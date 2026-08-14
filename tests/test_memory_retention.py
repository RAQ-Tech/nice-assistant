import tempfile
import unittest
from pathlib import Path

from app.repositories import UnitOfWork, now_ts
from tests.support import TestApp


class MemoryRetentionTests(unittest.TestCase):
    """ADR 0015 separates reversible forget from permanent deletion. Automatic expiry has
    to respect that line, and must not start deleting content on upgrade."""

    def _memory(self, running, status: str, age_days: int, content: str) -> str:
        created = running.client.post("/api/v1/memories", json={"scope": "global", "content": content})
        assert created.status_code == 200, created.text
        memory_id = created.json()["id"]
        with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
            from app.models import Memory

            row = uow.session.get(Memory, memory_id)
            row.status = status
            row.updated_at = now_ts() - age_days * 86400
        return memory_id

    def _statuses(self, running) -> dict:
        from app.models import Memory

        with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
            return {row.content: row.status for row in uow.session.query(Memory).all()}

    def test_expiry_is_off_unless_configured(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._memory(running, "rejected", 400, "very old rejected fact")
            self.assertEqual(running.services.memory.prune_discarded(0), 0)
            running.services.operations.startup_maintenance()
            self.assertIn("very old rejected fact", self._statuses(running))

    def test_rejected_and_forgotten_rows_past_the_window_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._memory(running, "rejected", 120, "old rejected")
            self._memory(running, "forgotten", 120, "old forgotten")
            removed = running.services.memory.prune_discarded(90)
            self.assertEqual(removed, 2)
            self.assertEqual(self._statuses(running), {})

    def test_live_and_recent_content_is_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._memory(running, "active", 400, "old but active")
            self._memory(running, "pending", 400, "old but pending")
            self._memory(running, "superseded", 400, "old but part of a revision chain")
            self._memory(running, "rejected", 10, "recently rejected")
            self.assertEqual(running.services.memory.prune_discarded(90), 0)
            self.assertEqual(len(self._statuses(running)), 4)

    def test_the_scheduled_pass_reports_the_configured_window(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            report = running.services.operations.storage_report()
            self.assertEqual(report["retention"]["memory_discard_days"], 0)


if __name__ == "__main__":
    unittest.main()
