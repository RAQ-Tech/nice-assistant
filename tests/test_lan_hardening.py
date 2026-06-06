import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import app.server as server


class LanHardeningApiTests(unittest.TestCase):
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
            "ALLOW_PUBLIC_SIGNUP": server.ALLOW_PUBLIC_SIGNUP,
        }
        server.DATA_DIR = base / "data"
        server.ARCHIVE_DIR = base / "archive"
        server.AUDIO_DIR = server.DATA_DIR / "audio"
        server.IMAGE_DIR = server.DATA_DIR / "images"
        server.VIDEO_DIR = server.DATA_DIR / "videos"
        server.LOG_DIR = server.DATA_DIR / "logs"
        server.STT_RECORDINGS_DIR = server.DATA_DIR / "stt_recordings"
        server.DB_PATH = server.DATA_DIR / "nice_assistant.db"
        server.ALLOW_PUBLIC_SIGNUP = False
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

    def create_user(self, username, password="pass1234", expected_status=200):
        status, payload, _headers = self.json_request(
            "POST",
            "/api/users",
            {"username": username, "password": password},
        )
        self.assertEqual(status, expected_status, payload)
        return payload

    def login_cookie(self, username, password="pass1234"):
        status, payload, headers = self.json_request(
            "POST",
            "/api/login",
            {"username": username, "password": password},
        )
        self.assertEqual(status, 200, payload)
        return headers.get("Set-Cookie").split(";", 1)[0], payload["userId"]

    def setting_payload(self, openai_api_key):
        return {
            "global_default_model": "",
            "default_memory_mode": "auto",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": openai_api_key,
            "onboarding_done": 0,
            "preferences_json": "{}",
        }

    def stored_openai_key(self, user_id):
        conn = server.db_conn()
        row = conn.execute("SELECT openai_api_key FROM app_settings WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        return row["openai_api_key"]

    def test_first_user_is_admin_and_second_signup_is_blocked_by_default(self):
        self.create_user("owner")
        conn = server.db_conn()
        owner = conn.execute("SELECT is_admin FROM users WHERE username='owner'").fetchone()
        conn.close()
        self.assertEqual(owner["is_admin"], 1)

        status, payload, _headers = self.json_request(
            "POST",
            "/api/users",
            {"username": "second", "password": "pass1234"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "Account creation is disabled after setup.")

        server.ALLOW_PUBLIC_SIGNUP = True
        self.create_user("second")
        conn = server.db_conn()
        second = conn.execute("SELECT is_admin FROM users WHERE username='second'").fetchone()
        conn.close()
        self.assertEqual(second["is_admin"], 0)

    def test_log_download_is_admin_only_and_redacted(self):
        server.ALLOW_PUBLIC_SIGNUP = True
        self.create_user("owner")
        self.create_user("member")
        owner_cookie, _owner_id = self.login_cookie("owner")
        member_cookie, _member_id = self.login_cookie("member")
        (server.LOG_DIR / "events.log").write_text(
            "\n".join(
                [
                    "openai_api_key=sk-testsecret1234567890",
                    "Authorization=Bearer abcdefghijklmnopqrstuvwxyz",
                    "Authorization=Basic dXNlcjpwYXNz",
                    "image_local_api_auth=alice:secret",
                    '{"image_local_api_auth": "bob:secret"}',
                ]
            ),
            encoding="utf-8",
        )

        status, payload, _headers = self.json_request("GET", "/api/logs/download", cookie=member_cookie)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "admin access required")

        status, raw, _headers = self.request("GET", "/api/logs/download", cookie=owner_cookie)
        body = raw.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertNotIn("sk-testsecret1234567890", body)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", body)
        self.assertNotIn("dXNlcjpwYXNz", body)
        self.assertNotIn("alice:secret", body)
        self.assertNotIn("bob:secret", body)
        self.assertIn("[REDACTED]", body)

    def test_settings_mask_and_preserve_openai_key(self):
        self.create_user("owner")
        cookie, user_id = self.login_cookie("owner")
        original_key = "sk-test-hardening-1234"

        status, payload, _headers = self.json_request(
            "POST",
            "/api/settings",
            self.setting_payload(original_key),
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)

        status, payload, _headers = self.json_request("GET", "/api/settings", cookie=cookie)
        self.assertEqual(status, 200, payload)
        masked_key = payload["settings"]["openai_api_key"]
        self.assertEqual(masked_key, "********1234")

        status, payload, _headers = self.json_request(
            "POST",
            "/api/settings",
            self.setting_payload(masked_key),
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.stored_openai_key(user_id), original_key)

        status, payload, _headers = self.json_request(
            "POST",
            "/api/settings",
            self.setting_payload(""),
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.stored_openai_key(user_id), original_key)

        replacement_key = "sk-test-replacement-9999"
        status, payload, _headers = self.json_request(
            "POST",
            "/api/settings",
            self.setting_payload(replacement_key),
            cookie=cookie,
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.stored_openai_key(user_id), replacement_key)

    def test_tts_audio_download_requires_login_and_matching_owner(self):
        server.ALLOW_PUBLIC_SIGNUP = True
        self.create_user("owner")
        self.create_user("member")
        owner_cookie, owner_id = self.login_cookie("owner")
        member_cookie, _member_id = self.login_cookie("member")
        audio_path = server.AUDIO_DIR / "owner.wav"
        audio_path.write_bytes(b"audio-bytes")
        conn = server.db_conn()
        conn.execute(
            "INSERT INTO audio_files(id,user_id,persona_id,chat_id,format,local_path,created_at) VALUES(?,?,?,?,?,?,?)",
            ("audio1", owner_id, None, None, "wav", str(audio_path), server.now_ts()),
        )
        conn.commit()
        conn.close()

        status, payload, _headers = self.json_request("GET", "/api/tts/audio/audio1")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

        status, payload, _headers = self.json_request("GET", "/api/tts/audio/audio1", cookie=member_cookie)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not found")

        status, raw, _headers = self.request("GET", "/api/tts/audio/audio1", cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"audio-bytes")

    def test_image_and_video_download_require_owner_or_legacy_prefix(self):
        server.ALLOW_PUBLIC_SIGNUP = True
        self.create_user("owner")
        self.create_user("member")
        owner_cookie, owner_id = self.login_cookie("owner")
        member_cookie, _member_id = self.login_cookie("member")

        image_path = server.IMAGE_DIR / "shared-name.png"
        image_path.write_bytes(b"image-bytes")
        video_path = server.VIDEO_DIR / "shared-name.mp4"
        video_path.write_bytes(b"video-bytes")
        conn = server.db_conn()
        conn.execute(
            "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) VALUES(?,?,?,?,?,?,?)",
            ("img1", owner_id, None, "image", image_path.name, str(image_path), server.now_ts()),
        )
        conn.execute(
            "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) VALUES(?,?,?,?,?,?,?)",
            ("vid1", owner_id, None, "video", video_path.name, str(video_path), server.now_ts()),
        )
        conn.commit()
        conn.close()

        status, payload, _headers = self.json_request("GET", f"/api/images/{image_path.name}")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

        status, payload, _headers = self.json_request("GET", f"/api/images/{image_path.name}", cookie=member_cookie)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not found")

        status, raw, _headers = self.request("GET", f"/api/images/{image_path.name}", cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"image-bytes")

        status, payload, _headers = self.json_request("GET", f"/api/videos/{video_path.name}", cookie=member_cookie)
        self.assertEqual(status, 404)

        status, raw, _headers = self.request("GET", f"/api/videos/{video_path.name}", cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"video-bytes")

        legacy_image = server.IMAGE_DIR / f"{owner_id}_legacy.png"
        legacy_image.write_bytes(b"legacy-image")
        status, raw, _headers = self.request("GET", f"/api/images/{legacy_image.name}", cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"legacy-image")

        status, payload, _headers = self.json_request("GET", f"/api/images/{legacy_image.name}", cookie=member_cookie)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not found")

    def test_generation_records_media_files(self):
        settings = {"openai_api_key": "sk-test-hardening-1234"}
        image_prefs = {"image_provider": "openai", "image_size": "1024x1024", "image_quality": "none"}
        video_prefs = {"video_provider": "openai", "video_model": "sora-2", "video_size": "720x1280", "video_duration": "4"}

        with mock.patch("app.server.openai_image", return_value=b"image-bytes"):
            _reply, image_url = server.generate_image_reply("draw a cat", "user1", "chat1", settings, image_prefs)
        with mock.patch("app.server.openai_video", return_value=(b"video-bytes", ".mp4")):
            _reply, video_url = server.generate_video_reply("make a cat video", "user1", "chat1", settings, video_prefs)

        image_name = image_url.rsplit("/", 1)[-1]
        video_name = video_url.rsplit("/", 1)[-1]
        conn = server.db_conn()
        image_row = conn.execute("SELECT * FROM media_files WHERE kind='image' AND filename=?", (image_name,)).fetchone()
        video_row = conn.execute("SELECT * FROM media_files WHERE kind='video' AND filename=?", (video_name,)).fetchone()
        conn.close()
        self.assertEqual(image_row["user_id"], "user1")
        self.assertEqual(video_row["user_id"], "user1")


if __name__ == "__main__":
    unittest.main()
