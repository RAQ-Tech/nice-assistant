import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import app.server as server


class ProviderReadinessApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.old_globals = {
            "DATA_DIR": server.DATA_DIR,
            "ARCHIVE_DIR": server.ARCHIVE_DIR,
            "AUDIO_DIR": server.AUDIO_DIR,
            "IMAGE_DIR": server.IMAGE_DIR,
            "VIDEO_DIR": server.VIDEO_DIR,
            "LOG_DIR": server.LOG_DIR,
            "STT_RECORDINGS_DIR": server.STT_RECORDINGS_DIR,
            "DB_PATH": server.DB_PATH,
            "SETTINGS_JSON": server.SETTINGS_JSON,
            "BACKUP_DIR": server.BACKUP_DIR,
            "ALLOW_PUBLIC_SIGNUP": server.ALLOW_PUBLIC_SIGNUP,
            "PROVIDER_TEST_TIMEOUT_SECONDS": server.PROVIDER_TEST_TIMEOUT_SECONDS,
        }
        server.DATA_DIR = base / "data"
        server.ARCHIVE_DIR = base / "archive"
        server.AUDIO_DIR = server.DATA_DIR / "audio"
        server.IMAGE_DIR = server.DATA_DIR / "images"
        server.VIDEO_DIR = server.DATA_DIR / "videos"
        server.LOG_DIR = server.DATA_DIR / "logs"
        server.STT_RECORDINGS_DIR = server.DATA_DIR / "stt_recordings"
        server.DB_PATH = server.DATA_DIR / "nice_assistant.db"
        server.SETTINGS_JSON = server.DATA_DIR / "settings.json"
        server.BACKUP_DIR = server.ARCHIVE_DIR / "backups"
        server.ALLOW_PUBLIC_SIGNUP = True
        server.PROVIDER_TEST_TIMEOUT_SECONDS = 1
        server.ensure_dirs()
        server.init_db()
        self.httpd = server.GracefulThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()
        for name, value in self.old_globals.items():
            setattr(server, name, value)
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def json_request(self, method, path, body=None, cookie=None):
        status, raw, headers = self.request(method, path, body=body, cookie=cookie)
        payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        return status, payload, headers

    def create_user(self, username="owner"):
        status, payload, _headers = self.json_request(
            "POST",
            "/api/users",
            {"username": username, "password": "pass1234"},
        )
        self.assertEqual(status, 200, payload)

    def login_cookie(self, username="owner"):
        status, payload, headers = self.json_request(
            "POST",
            "/api/login",
            {"username": username, "password": "pass1234"},
        )
        self.assertEqual(status, 200, payload)
        return headers.get("Set-Cookie").split(";", 1)[0], payload["userId"]

    def logged_in_cookie(self):
        self.create_user()
        cookie, _uid = self.login_cookie()
        return cookie

    def setting_payload(self, openai_api_key="", preferences=None):
        return {
            "global_default_model": "",
            "default_memory_mode": "auto",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": openai_api_key,
            "onboarding_done": 0,
            "preferences_json": json.dumps(preferences or {}),
        }

    def test_provider_test_requires_login_and_known_provider(self):
        status, payload, _headers = self.json_request(
            "POST",
            "/api/providers/test",
            {"provider": "openai"},
        )
        self.assertEqual(status, 401, payload)
        self.assertEqual(payload["error"], "unauthorized")

        cookie = self.logged_in_cookie()
        status, payload, _headers = self.json_request(
            "POST",
            "/api/providers/test",
            {"provider": "bogus"},
            cookie=cookie,
        )
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["error"], "unknown provider")

    def test_openai_missing_key_reports_clear_failure(self):
        cookie = self.logged_in_cookie()

        status, payload, _headers = self.json_request(
            "POST",
            "/api/providers/test",
            {"provider": "openai", "settings": self.setting_payload("")},
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "missing")
        self.assertIn("not configured", payload["message"])

    def test_openai_test_preserves_masked_key_and_accepts_unsaved_new_key(self):
        cookie = self.logged_in_cookie()
        stored_key = "sk-stored-readiness-1234"
        status, payload, _headers = self.json_request(
            "POST",
            "/api/settings",
            self.setting_payload(stored_key),
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)

        with mock.patch("app.server._openai_get_json", return_value={"data": [{"id": "gpt-test"}]}) as mock_get:
            status, payload, _headers = self.json_request(
                "POST",
                "/api/providers/test",
                {"provider": "openai", "settings": self.setting_payload("********1234")},
                cookie=cookie,
            )
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(mock_get.call_args.args[1], stored_key)

        unsaved_key = "sk-unsaved-readiness-9999"
        with mock.patch("app.server._openai_get_json", return_value={"data": [{"id": "gpt-test"}]}) as mock_get:
            status, payload, _headers = self.json_request(
                "POST",
                "/api/providers/test",
                {"provider": "openai", "settings": self.setting_payload(unsaved_key)},
                cookie=cookie,
            )
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(mock_get.call_args.args[1], unsaved_key)

    def test_provider_errors_are_redacted(self):
        cookie = self.logged_in_cookie()
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/models",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"bad sk-testsecret123456 token","Authorization":"Bearer abcdefghijklmnop"}'),
        )
        with mock.patch("app.server._openai_get_json", side_effect=error):
            status, payload, _headers = self.json_request(
                "POST",
                "/api/providers/test",
                {"provider": "openai", "settings": self.setting_payload("sk-testsecret123456")},
                cookie=cookie,
            )
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("sk-testsecret123456", payload["detail"])
        self.assertNotIn("abcdefghijklmnop", payload["detail"])
        self.assertIn("REDACTED", payload["detail"])

    def test_kokoro_automatic1111_and_comfyui_success_use_unsaved_settings(self):
        cookie = self.logged_in_cookie()
        calls = []

        def fake_provider_get_json(url, headers=None, timeout=None):
            calls.append((url, headers or {}, timeout))
            if url.endswith("/v1/audio/voices"):
                return {"voices": [{"id": "af_heart"}]}
            return {}

        with mock.patch("app.server.provider_get_json", side_effect=fake_provider_get_json):
            for provider, preferences in [
                ("kokoro", {"tts_local_base_url": "http://kokoro.local:8880"}),
                (
                    "automatic1111",
                    {
                        "image_local_base_url": "http://sd.local:7860",
                        "image_local_api_auth": "alice:secret",
                    },
                ),
                ("comfyui", {"image_local_base_url": "http://comfy.local:8188"}),
            ]:
                status, payload, _headers = self.json_request(
                    "POST",
                    "/api/providers/test",
                    {"provider": provider, "settings": self.setting_payload(preferences=preferences)},
                    cookie=cookie,
                )
                self.assertEqual(status, 200, payload)
                self.assertTrue(payload["ok"], payload)
                self.assertEqual(payload["status"], "ready")

        self.assertEqual(calls[0][0], "http://kokoro.local:8880/v1/audio/voices")
        self.assertEqual(calls[1][0], "http://sd.local:7860/sdapi/v1/options")
        self.assertTrue(calls[1][1].get("Authorization", "").startswith("Basic "))
        self.assertEqual(calls[2][0], "http://comfy.local:8188/system_stats")


if __name__ == "__main__":
    unittest.main()
