import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import app.auth as auth
import app.chat as chat
import app.media as media
import app.providers as providers
import app.server as server
import app.settings as settings
from app.storage import BackupStore, backup_name_from_api_path, read_json, safe_name


class RefactoredModuleContractTests(unittest.TestCase):
    def test_server_keeps_refactored_helper_compatibility_surface(self):
        password_hash = server.hash_password("pass1234")
        self.assertTrue(server.verify_password("pass1234", password_hash))
        self.assertTrue(auth.verify_password("pass1234", password_hash))

        sensitive = "openai_api_key=sk-contractsecret123456 bearer abcdefghijklmnop"
        self.assertEqual(server.redact_sensitive_text(sensitive), auth.redact_sensitive_text(sensitive))
        self.assertNotIn("sk-contractsecret123456", server.redact_sensitive_text(sensitive))

        settings_row = {"openai_api_key": "sk-contract123456", "preferences_json": "{}"}
        self.assertEqual(server.settings_for_response(settings_row), settings.settings_for_response(settings_row))
        self.assertEqual(server.settings_for_response(settings_row)["openai_api_key"], "********3456")

        self.assertIs(server.generate_chat_title, chat.generate_chat_title)
        self.assertIs(server.chat_title_needs_autogeneration, chat.chat_title_needs_autogeneration)
        self.assertIs(server.normalize_image_quality, media.normalize_image_quality)
        self.assertIs(server.user_safe_image_error, media.user_safe_image_error)
        self.assertIs(server.parse_preferences_json, settings.parse_preferences_json)

    def test_server_wrappers_honor_mutable_server_configuration(self):
        old_base_url = server.AUTOMATIC1111_BASE_URL
        old_timeout = server.PROVIDER_TEST_TIMEOUT_SECONDS
        try:
            server.AUTOMATIC1111_BASE_URL = "http://image.local:7860"
            self.assertEqual(server.normalize_local_image_base_url(""), "http://image.local:7860")

            class FakeResponse:
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, _exc_type, _exc, _tb):
                    return False

                def read(self):
                    return b'{"ok": true}'

            observed = {}

            def fake_urlopen(req, timeout=None):
                observed["url"] = req.full_url
                observed["timeout"] = timeout
                return FakeResponse()

            server.PROVIDER_TEST_TIMEOUT_SECONDS = 2.5
            with mock.patch("app.providers.urllib.request.urlopen", side_effect=fake_urlopen):
                payload = server.provider_get_json("http://provider.local/status")

            self.assertEqual(payload, {"ok": True})
            self.assertEqual(observed, {"url": "http://provider.local/status", "timeout": 2.5})
        finally:
            server.AUTOMATIC1111_BASE_URL = old_base_url
            server.PROVIDER_TEST_TIMEOUT_SECONDS = old_timeout

    def test_extracted_provider_and_storage_helpers_keep_contracts(self):
        response = providers.provider_test_response("openai", True, "ready", "OpenAI is reachable.", "token sk-secret123456")
        self.assertEqual(set(response), {"ok", "provider", "status", "message", "detail", "checkedAt"})
        self.assertTrue(response["ok"])
        self.assertNotIn("sk-secret123456", response["detail"])

        self.assertEqual(providers.normalize_provider_base_url("", "http://localhost:11434/"), "http://localhost:11434")
        self.assertEqual(providers.voice_ids_from_payload({"data": [{"id": "af_heart"}, {"name": "bf_echo"}]}), ["af_heart", "bf_echo"])

        self.assertEqual(safe_name("bad ../ name", "fallback"), "bad_.._name")
        self.assertEqual(safe_name("   !!!   ", "fallback"), "fallback")
        self.assertEqual(backup_name_from_api_path("/api/admin/backups/snapshot.zip/download", expect_download=True), "snapshot.zip")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(read_json(path, {"fallback": True}), {"fallback": True})

    def test_backup_store_creates_restorable_snapshot_without_server_globals(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "nice_assistant.db"
            settings_json = base / "settings.json"
            backup_dir = base / "backups"
            image_dir = base / "images"
            audio_dir = base / "audio"
            image_dir.mkdir()
            audio_dir.mkdir()

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE sample(id TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sample(id, value) VALUES('one', 'restored')")
            conn.commit()
            conn.close()

            settings_json.write_text(json.dumps({"example": True}), encoding="utf-8")
            (image_dir / "picture.png").write_bytes(b"image")
            (audio_dir / "clip.wav").write_bytes(b"audio")
            symlink_created = False
            try:
                (image_dir / "linked-secret.txt").symlink_to(settings_json)
                symlink_created = True
            except OSError:
                symlink_created = False

            store = BackupStore(
                db_path=db_path,
                settings_json=settings_json,
                backup_dir=backup_dir,
                media_dirs=(("images", image_dir), ("audio", audio_dir)),
                snapshot_limit=5,
                now_ts=lambda: 1_700_000_000,
            )

            self.assertIsNone(store.backup_path_for_name("../bad.zip"))
            backup = store.create_backup_snapshot(include_media=True)
            backup_path = backup_dir / backup["name"]

            with zipfile.ZipFile(backup_path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("nice_assistant.db", names)
                self.assertIn("settings.json", names)
                self.assertIn("data/images/picture.png", names)
                self.assertIn("data/audio/clip.wav", names)
                if symlink_created:
                    self.assertNotIn("data/images/linked-secret.txt", names)
                restored_db = base / "restored.db"
                restored_db.write_bytes(zf.read("nice_assistant.db"))

            restored = sqlite3.connect(restored_db)
            row = restored.execute("SELECT value FROM sample WHERE id='one'").fetchone()
            restored.close()
            self.assertEqual(row[0], "restored")


if __name__ == "__main__":
    unittest.main()
