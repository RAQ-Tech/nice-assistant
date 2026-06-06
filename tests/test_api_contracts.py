import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import app.server as server


class ApiContractTests(unittest.TestCase):
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

    def create_user(self, username, password="pass1234"):
        status, payload, _headers = self.json_request("POST", "/api/users", {"username": username, "password": password})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, {"ok": True})

    def login_cookie(self, username, password="pass1234"):
        status, payload, headers = self.json_request("POST", "/api/login", {"username": username, "password": password})
        self.assertEqual(status, 200, payload)
        self.assertEqual(set(payload), {"ok", "userId", "expiresAt", "ttlSeconds"})
        self.assertTrue(payload["ok"])
        return headers.get("Set-Cookie").split(";", 1)[0], payload["userId"]

    def create_logged_in_user(self, username="owner"):
        self.create_user(username)
        return self.login_cookie(username)

    def wait_for_job(self, cookie, job_id, timeout=15):
        deadline = time.monotonic() + timeout
        last_payload = None
        while time.monotonic() < deadline:
            status, payload, _headers = self.json_request("GET", f"/api/jobs/{job_id}", cookie=cookie)
            self.assertEqual(status, 200, payload)
            last_payload = payload
            status_value = payload["job"]["status"]
            if status_value in {"completed", "failed", "cancelled"}:
                return payload["job"]
            time.sleep(0.05)
        self.fail(f"job {job_id} did not finish: {last_payload}")

    def test_session_and_settings_contract(self):
        cookie, _uid = self.create_logged_in_user()

        status, session, _headers = self.json_request("GET", "/api/session", cookie=cookie)
        self.assertEqual(status, 200, session)
        self.assertEqual(set(session), {"expiresAt", "ttlSeconds", "now", "isAdmin"})
        self.assertTrue(session["isAdmin"])
        self.assertIsInstance(session["now"], int)

        settings_payload = {
            "global_default_model": "llama3",
            "default_memory_mode": "auto",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": "sk-contract123456",
            "onboarding_done": 1,
            "preferences_json": json.dumps({"general_auto_logout": True}),
        }
        status, payload, _headers = self.json_request("POST", "/api/settings", settings_payload, cookie=cookie)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, {"ok": True})

        status, payload, _headers = self.json_request("GET", "/api/settings", cookie=cookie)
        self.assertEqual(status, 200, payload)
        self.assertEqual(set(payload), {"settings"})
        returned = payload["settings"]
        for key in ("global_default_model", "default_memory_mode", "stt_provider", "tts_provider", "tts_format", "openai_api_key", "preferences_json"):
            self.assertIn(key, returned)
        self.assertEqual(returned["openai_api_key"], "********3456")
        self.assertNotIn("sk-contract123456", json.dumps(payload))

    def test_workspace_persona_and_chat_contract(self):
        cookie, _uid = self.create_logged_in_user()

        status, workspace, _headers = self.json_request("POST", "/api/workspaces", {"name": "Studio"}, cookie=cookie)
        self.assertEqual(status, 200, workspace)
        self.assertEqual(set(workspace), {"id"})
        workspace_id = workspace["id"]

        status, persona, _headers = self.json_request(
            "POST",
            "/api/personas",
            {"workspaceId": workspace_id, "name": "Guide", "traits": {"warmth": 70}},
            cookie=cookie,
        )
        self.assertEqual(status, 200, persona)
        self.assertEqual(set(persona), {"id"})
        persona_id = persona["id"]

        status, workspaces, _headers = self.json_request("GET", "/api/workspaces", cookie=cookie)
        self.assertEqual(status, 200, workspaces)
        self.assertEqual(set(workspaces), {"items"})
        self.assertEqual(workspaces["items"][0]["id"], workspace_id)
        self.assertEqual(workspaces["items"][0]["name"], "Studio")

        status, personas, _headers = self.json_request("GET", "/api/personas", cookie=cookie)
        self.assertEqual(status, 200, personas)
        self.assertEqual(set(personas), {"items"})
        returned_persona = personas["items"][0]
        self.assertEqual(returned_persona["id"], persona_id)
        self.assertEqual(returned_persona["workspace_ids"], [workspace_id])

        status, chat, _headers = self.json_request(
            "POST",
            "/api/chats",
            {"workspaceId": workspace_id, "personaId": persona_id, "title": "Contract chat", "memoryMode": "auto"},
            cookie=cookie,
        )
        self.assertEqual(status, 200, chat)
        self.assertEqual(set(chat), {"id"})
        chat_id = chat["id"]

        status, chats, _headers = self.json_request("GET", "/api/chats", cookie=cookie)
        self.assertEqual(status, 200, chats)
        self.assertEqual(set(chats), {"items"})
        self.assertEqual(chats["items"][0]["id"], chat_id)
        self.assertEqual(chats["items"][0]["title"], "Contract chat")

        status, detail, _headers = self.json_request("GET", f"/api/chats/{chat_id}", cookie=cookie)
        self.assertEqual(status, 200, detail)
        self.assertEqual(set(detail), {"chat", "messages"})
        self.assertEqual(detail["chat"]["id"], chat_id)
        self.assertEqual(detail["chat"]["workspace_id"], workspace_id)
        self.assertEqual(detail["chat"]["persona_id"], persona_id)
        self.assertEqual(detail["messages"], [])

    def test_async_job_contract_and_owner_scope(self):
        owner_cookie, _owner_id = self.create_logged_in_user("owner")
        self.create_user("member")
        member_cookie, _member_id = self.login_cookie("member")

        status, start, _headers = self.json_request(
            "POST",
            "/api/images/generate",
            {"prompt": "draw a contract cat", "async": True},
            cookie=owner_cookie,
        )
        self.assertEqual(status, 202, start)
        self.assertEqual(set(start), {"ok", "jobId", "chatId", "status"})
        self.assertTrue(start["ok"])
        self.assertEqual(start["status"], "queued")
        self.assertIsInstance(start["jobId"], str)

        status, payload, _headers = self.json_request("GET", f"/api/jobs/{start['jobId']}", cookie=member_cookie)
        self.assertEqual(status, 404, payload)
        self.assertEqual(payload, {"error": "not found"})

        job = self.wait_for_job(owner_cookie, start["jobId"])
        self.assertEqual(set(job), {"id", "kind", "status", "chatId", "progress", "queuePosition", "result", "error", "cancelRequested"})
        self.assertEqual(job["id"], start["jobId"])
        self.assertEqual(job["kind"], "image")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["chatId"], start["chatId"])
        self.assertEqual(job["progress"], "Completed")
        self.assertIsNone(job["queuePosition"])
        self.assertFalse(job["cancelRequested"])
        self.assertEqual(set(job["result"]), {"ok", "text", "chatId"})
        self.assertFalse(job["result"]["ok"])
        self.assertIn("image generation is currently disabled", job["result"]["text"])


if __name__ == "__main__":
    unittest.main()
