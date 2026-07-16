import json
import sqlite3
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from app import auth, chat, media, providers, settings
from app.provider_contracts import CancellationToken, ProviderError
from app.speech_clients import openai_speech
from app.storage import BackupStore, backup_name_from_api_path, read_json, safe_name


class RefactoredModuleContractTests(unittest.TestCase):
    def test_openai_speech_uses_documented_request_contract(self):
        observed = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"audio"

        def fake_urlopen(request, timeout=None):
            observed["payload"] = json.loads(request.data.decode())
            observed["timeout"] = timeout
            return FakeResponse()

        with mock.patch("app.speech_clients.urllib.request.urlopen", side_effect=fake_urlopen):
            audio = openai_speech(
                "Hello",
                "marin",
                "wav",
                "sk-contract123456",
                instructions="Speak warmly.",
            )
        self.assertEqual(audio, b"audio")
        self.assertEqual(observed["payload"]["response_format"], "wav")
        self.assertEqual(observed["payload"]["instructions"], "Speak warmly.")
        self.assertNotIn("format", observed["payload"])
        self.assertEqual(observed["timeout"], 120)

    def test_extracted_helpers_keep_safe_contracts(self):
        password_hash = auth.hash_password("pass1234")
        self.assertTrue(auth.verify_password("pass1234", password_hash))
        sensitive = "openai_api_key=sk-contractsecret123456 bearer abcdefghijklmnop"
        self.assertNotIn("sk-contractsecret123456", auth.redact_sensitive_text(sensitive))
        self.assertEqual(chat.generate_chat_title("Please explain durable queues"), "explain durable queues")
        self.assertTrue(chat.chat_title_needs_autogeneration("New chat"))
        self.assertTrue(chat.chat_title_needs_autogeneration("New conversation"))
        self.assertTrue(chat.chat_title_needs_autogeneration("Untitled chat"))
        self.assertFalse(chat.chat_title_needs_autogeneration("Greenhouse at sunrise"))
        self.assertEqual(media.normalize_image_quality("hd"), "high")
        self.assertEqual(settings.parse_preferences_json('{"x":1}'), {"x": 1})
        response = providers.provider_test_response(
            "openai", True, "ready", "OpenAI is reachable.", "token sk-secret123456"
        )
        self.assertNotIn("sk-secret123456", response["detail"])
        error = urllib.error.HTTPError("https://provider.invalid", 401, "secret", {}, None)
        message = providers.user_safe_provider_error("TTS", "Example", error)
        self.assertIn("credentials", message)

    def test_cancellation_token_callbacks_and_provider_error_shape(self):
        token = CancellationToken()
        called = []
        token.register(lambda: called.append(True))
        token.cancel()
        token.cancel()
        self.assertEqual(called, [True])
        with self.assertRaises(ProviderError):
            token.raise_if_cancelled()
        error = ProviderError(provider="test", code="failed", user_message="Safe", retryable=True)
        self.assertEqual(
            error.as_dict(),
            {"code": "failed", "message": "Safe", "provider": "test", "retryable": True, "request_id": None},
        )

    def test_provider_and_storage_helpers(self):
        self.assertEqual(providers.normalize_provider_base_url("", "http://localhost:11434/"), "http://localhost:11434")
        self.assertEqual(
            providers.voice_ids_from_payload({"data": [{"id": "af_heart"}, {"name": "bf_echo"}]}),
            ["af_heart", "bf_echo"],
        )
        self.assertEqual(safe_name("bad ../ name", "fallback"), "bad_.._name")
        self.assertEqual(safe_name("   !!!   ", "fallback"), "fallback")
        self.assertEqual(
            backup_name_from_api_path("/api/v1/admin/backups/snapshot.zip/download", expect_download=True),
            "snapshot.zip",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(read_json(path, {"fallback": True}), {"fallback": True})

    def test_backup_store_creates_restorable_snapshot_without_globals(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "nice_assistant.db"
            settings_json = base / "settings.json"
            backup_dir = base / "backups"
            image_dir = base / "images"
            audio_dir = base / "audio"
            image_dir.mkdir()
            audio_dir.mkdir()
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE sample(id TEXT PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO sample VALUES('one', 'restored')")
            connection.commit()
            connection.close()
            settings_json.write_text(json.dumps({"example": True}), encoding="utf-8")
            (image_dir / "picture.png").write_bytes(b"image")
            (audio_dir / "clip.wav").write_bytes(b"audio")
            store = BackupStore(
                db_path=db_path,
                settings_json=settings_json,
                backup_dir=backup_dir,
                media_dirs=(("images", image_dir), ("audio", audio_dir)),
                snapshot_limit=5,
                now_ts=lambda: 1_700_000_000,
            )
            backup = store.create_backup_snapshot(include_media=True)
            with zipfile.ZipFile(backup_dir / backup["name"], "r") as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertIn("data/images/picture.png", archive.namelist())
                restored_db = base / "restored.db"
                restored_db.write_bytes(archive.read("nice_assistant.db"))
            restored = sqlite3.connect(restored_db)
            self.assertEqual(restored.execute("SELECT value FROM sample").fetchone()[0], "restored")
            restored.close()


if __name__ == "__main__":
    unittest.main()
