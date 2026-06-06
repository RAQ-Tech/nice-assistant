import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import app.server as server


class AsyncJobsApiTests(unittest.TestCase):
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
        status, payload, _headers = self.json_request(
            "POST",
            "/api/users",
            {"username": username, "password": password},
        )
        self.assertEqual(status, 200, payload)

    def login_cookie(self, username, password="pass1234"):
        status, payload, headers = self.json_request(
            "POST",
            "/api/login",
            {"username": username, "password": password},
        )
        self.assertEqual(status, 200, payload)
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

    def test_async_image_and_video_start_return_202_and_complete(self):
        cookie, _uid = self.create_logged_in_user()

        status, payload, _headers = self.json_request(
            "POST",
            "/api/images/generate",
            {"prompt": "draw a cat", "async": True},
            cookie=cookie,
        )
        self.assertEqual(status, 202, payload)
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["jobId"])
        image_job = self.wait_for_job(cookie, payload["jobId"])
        self.assertEqual(image_job["kind"], "image")
        self.assertEqual(image_job["status"], "completed")
        self.assertFalse(image_job["result"]["ok"])
        self.assertIn("disabled", image_job["result"]["text"].lower())

        status, payload, _headers = self.json_request(
            "POST",
            "/api/videos/generate",
            {"prompt": "make a cat video", "async": True},
            cookie=cookie,
        )
        self.assertEqual(status, 202, payload)
        video_job = self.wait_for_job(cookie, payload["jobId"])
        self.assertEqual(video_job["kind"], "video")
        self.assertEqual(video_job["status"], "completed")
        self.assertFalse(video_job["result"]["ok"])
        self.assertIn("disabled", video_job["result"]["text"].lower())

    def test_async_chat_completion_exposes_result_and_updates_chat(self):
        cookie, _uid = self.create_logged_in_user()

        with mock.patch("app.server.call_ollama", return_value="Async chat test reply."):
            status, payload, _headers = self.json_request(
                "POST",
                "/api/chat",
                {"text": "tell me about reliable async tests", "async": True},
                cookie=cookie,
            )
            self.assertEqual(status, 202, payload)
            self.assertTrue(payload["jobId"])
            self.assertTrue(payload["chatId"])

            job = self.wait_for_job(cookie, payload["jobId"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["chatId"], payload["chatId"])
        self.assertEqual(job["result"]["text"], "Async chat test reply.")

        status, detail, _headers = self.json_request("GET", f"/api/chats/{payload['chatId']}", cookie=cookie)
        self.assertEqual(status, 200, detail)
        roles = [m["role"] for m in detail["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertIn("reliable async tests", detail["messages"][0]["text"])
        self.assertEqual(detail["messages"][1]["text"], "Async chat test reply.")

    def test_job_status_is_owner_scoped(self):
        self.create_user("owner")
        self.create_user("member")
        owner_cookie, _owner_id = self.login_cookie("owner")
        member_cookie, _member_id = self.login_cookie("member")

        status, payload, _headers = self.json_request(
            "POST",
            "/api/images/generate",
            {"prompt": "draw a cat", "async": True},
            cookie=owner_cookie,
        )
        self.assertEqual(status, 202, payload)

        status, member_payload, _headers = self.json_request("GET", f"/api/jobs/{payload['jobId']}", cookie=member_cookie)
        self.assertEqual(status, 404)
        self.assertEqual(member_payload["error"], "not found")

        owner_job = self.wait_for_job(owner_cookie, payload["jobId"])
        self.assertEqual(owner_job["status"], "completed")

    def test_failed_job_exposes_safe_error(self):
        cookie, _uid = self.create_logged_in_user()

        with mock.patch("app.server.generate_image_reply", side_effect=RuntimeError("provider leaked sk-testsecret123456")):
            status, payload, _headers = self.json_request(
                "POST",
                "/api/images/generate",
                {"prompt": "draw a cat", "async": True},
                cookie=cookie,
            )
            self.assertEqual(status, 202, payload)
            job = self.wait_for_job(cookie, payload["jobId"])

        self.assertEqual(job["status"], "failed")
        self.assertNotIn("sk-testsecret123456", job["error"])
        self.assertIn("REDACTED", job["error"])

    def test_delete_job_cancels_queued_and_running_rows(self):
        cookie, uid = self.create_logged_in_user()
        queued_id = server.create_async_job(uid, None, "image")
        running_id = server.create_async_job(uid, None, "chat")
        server.update_async_job(running_id, status="running", started_at=server.now_ts(), progress="Running")

        status, queued_payload, _headers = self.json_request("DELETE", f"/api/jobs/{queued_id}", cookie=cookie)
        self.assertEqual(status, 200, queued_payload)
        self.assertEqual(queued_payload["job"]["status"], "cancelled")
        self.assertTrue(queued_payload["job"]["cancelRequested"])

        status, running_payload, _headers = self.json_request("DELETE", f"/api/jobs/{running_id}", cookie=cookie)
        self.assertEqual(status, 200, running_payload)
        self.assertEqual(running_payload["job"]["status"], "cancelled")
        self.assertTrue(running_payload["job"]["cancelRequested"])

    def test_init_db_marks_stale_jobs_failed(self):
        _cookie, uid = self.create_logged_in_user()
        conn = server.db_conn()
        for job_id, status in [("queued-stale", "queued"), ("running-stale", "running")]:
            conn.execute(
                """
                INSERT INTO async_jobs(id,user_id,chat_id,kind,status,cancel_requested,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (job_id, uid, None, "chat", status, 0, server.now_ts(), server.now_ts()),
            )
        conn.commit()
        conn.close()

        server.init_db()

        conn = server.db_conn()
        rows = {
            row["id"]: row
            for row in conn.execute("SELECT id, status, error FROM async_jobs WHERE id IN ('queued-stale','running-stale')").fetchall()
        }
        conn.close()
        self.assertEqual(rows["queued-stale"]["status"], "failed")
        self.assertEqual(rows["running-stale"]["status"], "failed")
        self.assertEqual(rows["queued-stale"]["error"], "interrupted by server restart")
        self.assertEqual(rows["running-stale"]["error"], "interrupted by server restart")


if __name__ == "__main__":
    unittest.main()
