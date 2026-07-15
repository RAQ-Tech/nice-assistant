import tempfile
import unittest
from pathlib import Path

from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


class AsgiApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = FakeChatProvider(["hello ", "world"])
        self.test_app = TestApp(Path(self.tmp.name), chat_provider=self.provider)
        self.running = self.test_app.__enter__()
        self.client = self.running.client

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_typed_resource_turn_stream_and_owner_scoped_media(self):
        self.assertEqual(self.client.get("/health").json(), {"ok": True, "runtime": "asgi"})
        self.assertEqual(self.client.get("/api/v1/settings").status_code, 401)
        owner_id = self.running.create_and_login()
        settings = self.client.put(
            "/api/v1/settings",
            json={
                "global_default_model": "fake-model",
                "openai_api_key": "sk-asgi-secret-1234",
                "preferences": {"general_auto_logout": False, "voice_speed": 1.1},
            },
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json()["openai_api_key"], "********1234")

        workspace = self.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = self.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace["id"], "name": "Guide", "traits": {"warmth": 60}},
        ).json()
        memory = self.client.post(
            "/api/v1/memories",
            json={"scope": "persona", "scope_id": persona["id"], "content": "Prefers short answers."},
        ).json()
        chat = self.client.post(
            "/api/v1/chats",
            json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
        ).json()
        started = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Say hello"},
        )
        self.assertEqual(started.status_code, 202, started.text)
        turn = started.json()["turn"]
        job = self.running.wait_job(started.json()["job"]["id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["text"], "hello world")

        with self.client.stream("GET", f"/api/v1/turns/{turn['id']}/events") as response:
            stream = "\n".join(response.iter_lines())
        self.assertIn("event: turn.snapshot", stream)
        self.assertIn("event: assistant.delta", stream)
        self.assertIn("event: turn.completed", stream)
        self.assertIn("hello ", stream)

        media_path = self.running.config.image_dir / "owned.png"
        media_path.write_bytes(b"owned-media")
        with UnitOfWork(
            self.running.services.runtime.session_factory, self.running.services.runtime.secret_store
        ) as uow:
            media = uow.repo.add_media(
                user_id=owner_id,
                chat_id=chat["id"],
                kind="image",
                filename=media_path.name,
                local_path=str(media_path),
            )
            media_id = media.id
        self.assertEqual(self.client.get(f"/api/v1/media/{media_id}").content, b"owned-media")
        media_library = self.client.get("/api/v1/media", params={"kind": "image"})
        self.assertEqual(media_library.status_code, 200, media_library.text)
        library_items = media_library.json()["items"]
        self.assertEqual(len(library_items), 1)
        created_at = library_items[0].pop("created_at")
        self.assertIsInstance(created_at, int)
        self.assertEqual(
            library_items[0],
            {
                "id": media_id,
                "chat_id": chat["id"],
                "kind": "image",
                "filename": "owned.png",
                "content_url": f"/api/v1/media/{media_id}",
            },
        )

        owner_cookie = self.client.cookies.get("nice_assistant_session")
        second_id = self.running.create_and_login("second")
        self.assertNotEqual(owner_id, second_id)
        self.assertEqual(self.client.get(f"/api/v1/chats/{chat['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/media/{media_id}").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/media", params={"kind": "image"}).json(), {"items": []})
        self.client.cookies.set("nice_assistant_session", owner_cookie)
        self.assertEqual(self.client.delete(f"/api/v1/memories/{memory['id']}").status_code, 200)

    def test_validation_openapi_limits_and_no_bridge_thread(self):
        response = self.client.post("/api/v1/users", json={"username": "x", "password": "short"})
        self.assertEqual(response.status_code, 422)
        schema = self.client.get("/api/v1/openapi.json")
        document = schema.json()
        self.assertIn("/api/v1/chats/{chat_id}/turns", document["paths"])
        self.assertIn("/api/v1/media", document["paths"])
        accepted_schema = document["paths"]["/api/v1/chats/{chat_id}/turns"]["post"]["responses"]["202"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(accepted_schema["$ref"], "#/components/schemas/TurnAcceptedResponse")
        self.assertIn("JobRepresentation", document["components"]["schemas"])
        self.assertIn("TurnRepresentation", document["components"]["schemas"])
        import threading

        self.assertFalse(any("legacy-compatibility" in thread.name for thread in threading.enumerate()))

    def test_asgi_rejects_large_body_before_route_parsing(self):
        self.test_app.__exit__(None, None, None)
        self.test_app = TestApp(Path(self.tmp.name) / "limited", json_limit=1024)
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        response = self.client.post(
            "/api/v1/users",
            json={"username": "owner", "password": "x" * 2000},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["message"], "request body too large")


if __name__ == "__main__":
    unittest.main()
