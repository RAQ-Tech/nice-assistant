import tempfile
import threading
import unittest
from pathlib import Path

from sqlalchemy import text

from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


class BulkDataActionTests(unittest.TestCase):
    def test_forget_and_delete_are_distinct_and_bulk_memory_delete_removes_history_and_fts(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            first = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "The first durable memory."},
            ).json()
            second = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "The second durable memory."},
            ).json()

            forgotten = running.client.post(
                "/api/v1/memories/bulk-actions",
                json={"action": "forget", "ids": [first["id"]]},
            )
            self.assertEqual(forgotten.status_code, 200, forgotten.text)
            self.assertEqual(forgotten.json()["affected_count"], 1)
            self.assertEqual(
                running.client.get(f"/api/v1/memories/{first['id']}/history").json()["memory"]["status"],
                "forgotten",
            )

            deleted = running.client.post(
                "/api/v1/memories/bulk-actions",
                json={"action": "delete", "ids": [first["id"], second["id"]]},
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(deleted.json()["affected_count"], 2)
            self.assertEqual(running.client.get("/api/v1/memories").json()["items"], [])
            self.assertEqual(running.client.get(f"/api/v1/memories/{first['id']}/history").status_code, 404)
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                fts_count = uow.session.scalar(
                    text("SELECT count(*) FROM memory_fts WHERE user_id=:user_id"),
                    {"user_id": user_id},
                )
            self.assertEqual(fts_count, 0)

    def test_bulk_memory_actions_are_atomic_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login("owner")
            owner_memory = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "Owner memory."},
            ).json()
            owner_cookie = running.client.cookies.get("nice_assistant_session")
            running.create_and_login("member")
            member_memory = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "Member memory."},
            ).json()

            denied = running.client.post(
                "/api/v1/memories/bulk-actions",
                json={"action": "delete", "ids": [member_memory["id"], owner_memory["id"]]},
            )
            self.assertEqual(denied.status_code, 404, denied.text)
            self.assertEqual(
                [item["id"] for item in running.client.get("/api/v1/memories").json()["items"]],
                [member_memory["id"]],
            )
            running.client.cookies.set("nice_assistant_session", owner_cookie)
            self.assertEqual(
                [item["id"] for item in running.client.get("/api/v1/memories").json()["items"]],
                [owner_memory["id"]],
            )

    def test_chat_hide_and_delete_are_distinct_bulk_actions(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            hidden = running.client.post("/api/v1/chats", json={"title": "Hide me"}).json()
            deleted = running.client.post("/api/v1/chats", json={"title": "Delete me"}).json()
            turn = running.client.post(
                f"/api/v1/chats/{deleted['id']}/turns",
                json={"text": "Create durable transcript state.", "memory_mode": "off"},
            ).json()
            self.assertEqual(running.wait_job(turn["job"]["id"])["status"], "completed")

            hidden_result = running.client.post(
                "/api/v1/chats/bulk-actions",
                json={"action": "hide", "ids": [hidden["id"]]},
            )
            self.assertEqual(hidden_result.status_code, 200, hidden_result.text)
            self.assertEqual(hidden_result.json()["affected_count"], 1)
            self.assertEqual(
                [item["id"] for item in running.client.get("/api/v1/chats").json()["items"]], [deleted["id"]]
            )
            self.assertEqual(running.client.get(f"/api/v1/chats/{hidden['id']}").status_code, 200)

            deleted_result = running.client.delete(f"/api/v1/chats/{deleted['id']}")
            self.assertEqual(deleted_result.status_code, 200, deleted_result.text)
            self.assertTrue(deleted_result.json()["deleted"])
            self.assertEqual(running.client.get(f"/api/v1/chats/{deleted['id']}").status_code, 404)
            retained_job = running.client.get(f"/api/v1/jobs/{turn['job']['id']}").json()
            self.assertIsNone(retained_job["chat_id"])
            self.assertIsNone(retained_job["turn_id"])

            purge_hidden = running.client.post(
                "/api/v1/chats/bulk-actions",
                json={"action": "delete", "ids": [hidden["id"]]},
            )
            self.assertEqual(purge_hidden.status_code, 200, purge_hidden.text)
            self.assertEqual(running.client.get(f"/api/v1/chats/{hidden['id']}").status_code, 404)

    def test_permanent_chat_delete_is_blocked_while_generation_is_active(self):
        gate = threading.Event()
        provider = FakeChatProvider(gate=gate)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Active chat"}).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Wait for the gate.", "memory_mode": "off"},
            ).json()
            self.assertTrue(provider.started.wait(2))

            blocked = running.client.delete(f"/api/v1/chats/{chat['id']}")
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("active work", blocked.json()["error"]["message"].lower())

            running.client.delete(f"/api/v1/jobs/{turn['job']['id']}")
            gate.set()
            self.assertEqual(running.wait_job(turn["job"]["id"])["status"], "cancelled")
            self.assertEqual(running.client.delete(f"/api/v1/chats/{chat['id']}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
