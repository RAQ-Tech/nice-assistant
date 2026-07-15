import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.asgi import create_app
from app.provider_registry import ProviderRegistry
from app.runtime import AppConfig
from app.secret_store import SecretStore
from tests.support import FakeChatProvider, TestApp, fast_hash, fast_verify


class LanHardeningTests(unittest.TestCase):
    def test_signup_closes_after_first_user_when_not_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = create_app(
                AppConfig(data_dir=base / "data", archive_dir=base / "archive", allow_public_signup=False),
                secret_store=SecretStore("test-key"),
                providers=ProviderRegistry(chat_providers={"ollama": FakeChatProvider()}),
                password_hasher=fast_hash,
                password_verifier=fast_verify,
            )
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                client.headers.update({"X-Nice-Assistant-CSRF": "1"})
                self.assertEqual(
                    client.post("/api/v1/users", json={"username": "owner", "password": "pass1234"}).status_code,
                    200,
                )
                blocked = client.post("/api/v1/users", json={"username": "other", "password": "pass1234"})
                self.assertEqual(blocked.status_code, 403)

    def test_cross_owner_resources_and_persona_scope_are_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            owner_id = running.create_and_login("owner")
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Owner"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Owner persona"},
            ).json()
            owner_cookie = running.client.cookies.get("nice_assistant_session")
            running.create_and_login("member")
            self.assertEqual(running.client.get(f"/api/v1/personas/{persona['id']}").status_code, 404)
            self.assertEqual(
                running.client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"]},
                ).status_code,
                404,
            )
            running.client.cookies.set("nice_assistant_session", owner_cookie)
            self.assertEqual(running.client.get(f"/api/v1/personas/{persona['id']}").status_code, 200)
            self.assertTrue(owner_id)

    def test_protected_media_uses_database_ownership_not_filename_prefix(self):
        from app.repositories import UnitOfWork

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            owner_id = running.create_and_login("owner")
            path = running.config.image_dir / "shared-name.png"
            path.write_bytes(b"image")
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                media = uow.repo.add_media(
                    user_id=owner_id,
                    chat_id=None,
                    kind="image",
                    filename=path.name,
                    local_path=str(path),
                )
                media_id = media.id
            owner_cookie = running.client.cookies.get("nice_assistant_session")
            running.create_and_login("member")
            self.assertEqual(running.client.get(f"/api/v1/media/{media_id}").status_code, 404)
            self.assertEqual(running.client.get(f"/api/images/{path.name}").status_code, 404)
            running.client.cookies.set("nice_assistant_session", owner_cookie)
            self.assertEqual(running.client.get(f"/api/v1/media/{media_id}").content, b"image")
            self.assertEqual(running.client.get(f"/api/images/{path.name}").status_code, 404)

    def test_tts_validates_persona_and_chat_ownership_and_protects_audio(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            owner_id = running.create_and_login("owner")
            running.client.put(
                "/api/v1/settings",
                json={"tts_provider": "openai", "openai_api_key": "sk-owner-key", "preferences": {}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Owner"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Voice"},
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={"workspace_id": workspace["id"], "persona_id": persona["id"]},
            ).json()
            with mock.patch("app.speech_service.openai_speech", return_value=b"audio"):
                made = running.client.post(
                    "/api/v1/speech/syntheses",
                    json={"text": "hello", "persona_id": persona["id"], "chat_id": chat["id"], "format": "wav"},
                )
            self.assertEqual(made.status_code, 200, made.text)
            audio_url = made.json()["audio_url"]
            self.assertEqual(running.client.get(audio_url).content, b"audio")
            owner_cookie = running.client.cookies.get("nice_assistant_session")
            running.create_and_login("member")
            self.assertEqual(running.client.get(audio_url).status_code, 404)
            with mock.patch("app.speech_service.openai_speech", return_value=b"audio"):
                denied = running.client.post(
                    "/api/v1/speech/syntheses",
                    json={"text": "hello", "persona_id": persona["id"]},
                )
            self.assertEqual(denied.status_code, 404)
            running.client.cookies.set("nice_assistant_session", owner_cookie)
            self.assertTrue(owner_id)

    def test_provider_errors_and_diagnostic_logs_redact_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.runtime.logger.critical("provider token sk-supersecret123456")
            response = running.client.get("/api/v1/admin/diagnostics/log")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("sk-supersecret123456", response.text)
            self.assertIn("REDACTED", response.text)


if __name__ == "__main__":
    unittest.main()
