import tempfile
import unittest
from pathlib import Path

from tests.support import TestApp


class BrowserApiContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_session_settings_workspace_persona_chat_and_memory_contracts(self):
        self.running.create_and_login()
        session = self.client.get("/api/v1/session")
        self.assertEqual(session.status_code, 200, session.text)
        self.assertEqual(set(session.json()), {"user_id", "expires_at", "ttl_seconds", "is_admin"})
        self.assertTrue(session.json()["is_admin"])

        settings_payload = {
            "global_default_model": "fake-model",
            "default_memory_mode": "saved",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": "sk-contract123456",
            "onboarding_done": True,
            "preferences": {"general_auto_logout": True},
        }
        saved = self.client.put("/api/v1/settings", json=settings_payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        returned = self.client.get("/api/v1/settings").json()
        self.assertEqual(returned["openai_api_key"], "********3456")
        self.assertEqual(returned["preferences"], {"general_auto_logout": True})
        self.assertEqual(returned["default_memory_mode"], "saved")
        invalid = {**settings_payload, "default_memory_mode": "auto"}
        self.assertEqual(self.client.put("/api/v1/settings", json=invalid).status_code, 422)

        workspace = self.client.post("/api/v1/workspaces", json={"name": "Studio"})
        self.assertEqual(workspace.status_code, 200, workspace.text)
        workspace_id = workspace.json()["id"]
        persona = self.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace_id, "name": "Guide", "traits": {"warmth": 70}},
        )
        self.assertEqual(persona.status_code, 200, persona.text)
        persona_id = persona.json()["id"]
        listed_persona = self.client.get("/api/v1/personas").json()["items"][0]
        self.assertEqual(listed_persona["workspace_ids"], [workspace_id])

        chat = self.client.post(
            "/api/v1/chats",
            json={
                "workspace_id": workspace_id,
                "persona_id": persona_id,
                "title": "Contract chat",
                "memory_mode": "saved",
            },
        )
        self.assertEqual(chat.status_code, 200, chat.text)
        chat_id = chat.json()["id"]
        detail = self.client.get(f"/api/v1/chats/{chat_id}").json()
        self.assertEqual(set(detail), {"chat", "messages"})
        self.assertEqual(detail["chat"]["workspace_id"], workspace_id)
        self.assertEqual(detail["messages"], [])

        memory = self.client.post(
            "/api/v1/memories",
            json={"scope": "chat", "scope_id": chat_id, "content": "Remember this."},
        )
        self.assertEqual(memory.status_code, 200, memory.text)
        memory_id = memory.json()["id"]
        memories = self.client.get("/api/v1/memories?status=active").json()["items"]
        self.assertEqual([item["id"] for item in memories], [memory_id])
        revised = self.client.put(
            f"/api/v1/memories/{memory_id}",
            json={"content": "Revised.", "scope": "chat", "scope_id": chat_id},
        )
        self.assertEqual(revised.status_code, 200, revised.text)
        self.assertEqual(revised.json()["content"], "Revised.")

    def test_jobs_are_owner_scoped_and_use_the_typed_shape(self):
        self.running.create_and_login("owner")
        started = self.client.post("/api/v1/media/image-jobs", json={"prompt": "draw a cat"})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(set(started.json()), {"job_id", "capability_request_id", "chat_id", "status"})
        owner_cookie = self.client.cookies.get("nice_assistant_session")

        self.running.create_and_login("member")
        self.assertEqual(self.client.get(f"/api/v1/jobs/{started.json()['job_id']}").status_code, 404)

        self.client.cookies.set("nice_assistant_session", owner_cookie)
        job = self.running.wait_job(started.json()["job_id"])
        self.assertEqual(
            set(job),
            {
                "id",
                "kind",
                "status",
                "chat_id",
                "turn_id",
                "capability_request_id",
                "progress",
                "queue_position",
                "result",
                "error",
                "cancel_requested",
                "created_at",
                "started_at",
                "completed_at",
            },
        )
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["result"])
        self.assertIn("disabled", job["error"].lower())
        capability = self.client.get(f"/api/v1/capability-requests/{started.json()['capability_request_id']}").json()
        self.assertEqual(capability["status"], "failed")

    def test_operational_contracts_and_legacy_surface_is_absent(self):
        self.running.create_and_login()
        backup = self.client.post("/api/v1/admin/backups", json={"include_media": False})
        self.assertEqual(backup.status_code, 200, backup.text)
        self.assertEqual(
            set(backup.json()),
            {"name", "size", "created_at", "created_at_iso", "include_media", "download_url"},
        )
        name = backup.json()["name"]
        self.assertEqual(self.client.get("/api/v1/admin/backups").json()["items"][0]["name"], name)
        self.assertEqual(self.client.get(f"/api/v1/admin/backups/{name}/download").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/admin/backups/{name}").json(), {"ok": True})
        self.assertEqual(
            self.client.post(
                "/api/v1/diagnostics/client-events",
                json={"message": "safe event"},
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/v1/admin/diagnostics/log").status_code, 200)
        for path in ("/api/session", "/api/settings", "/api/chat", "/api/tts/stream"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


if __name__ == "__main__":
    unittest.main()
