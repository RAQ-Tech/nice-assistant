#!/usr/bin/env python3
"""Process-level canonical API smoke check for Nice Assistant."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def _write_json(self, payload: dict, content_type="application/json"):
        body = json.dumps(payload).encode("utf-8") + (b"\n" if "ndjson" in content_type else b"")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):  # noqa: N802 - stdlib handler contract
        if self.path == "/api/tags":
            self._write_json({"models": [{"name": "smoke-model"}]})
            return
        if self.path == "/sdapi/v1/options":
            self._write_json({"sd_model_checkpoint": "smoke-model.safetensors"})
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802 - stdlib handler contract
        if self.path == "/sdapi/v1/txt2img":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._write_json(
                {
                    "images": [
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                    ]
                }
            )
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(raw.decode("utf-8"))
        messages = payload.get("messages") or []
        text = str(messages[-1].get("content") or "") if messages else ""
        system = str(messages[0].get("content") or "") if messages else ""
        if "hold cancellation" in text:
            time.sleep(2)
        if "Extract only stable facts" in system:
            content = '{"candidates":[]}'
        elif "explicitly available platform capabilities" in system:
            if "container identity portrait" in text.casefold():
                content = json.dumps(
                    {
                        "requests": [
                            {
                                "capability_key": "media.generate_image",
                                "prompt": "a container identity portrait",
                                "operation": "generate",
                                "domains": ["fantasy"],
                                "content_tags": ["general"],
                                "required_features": ["identity_control"],
                                "persona_subject": True,
                            }
                        ]
                    }
                )
            else:
                content = '{"requests":[]}'
        else:
            content = "Smoke model reply."
        self._write_json(
            {
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
            },
            "application/x-ndjson",
        )


class FakeOllamaServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="smoke-fake-ollama", daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, _exc_type, _exc, _traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def run_fake_ollama_server(port: int) -> int:
    server = ThreadingHTTPServer(("0.0.0.0", port), _FakeOllamaHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def request(method: str, base_url: str, path: str, body: dict | None = None, cookie: str | None = None):
    data = None
    headers = {}
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["X-Nice-Assistant-CSRF"] = "1"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def json_request(method: str, base_url: str, path: str, body: dict | None = None, cookie: str | None = None):
    status, raw, headers = request(method, base_url, path, body=body, cookie=cookie)
    payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    return status, payload, headers


def assert_status(actual: int, expected: int, label: str):
    if actual != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, got {actual}")


def wait_for_server(base_url: str, proc: subprocess.Popen[bytes]):
    deadline = time.monotonic() + 15
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited before becoming healthy: {proc.returncode}")
        try:
            status, payload, _headers = json_request("GET", base_url, "/health")
            if status == 200 and payload.get("ok") is True:
                return
        except Exception as exc:  # noqa: BLE001 - collect startup diagnostics
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"server did not become healthy: {last_error}")


def wait_for_job(base_url: str, job_id: str, cookie: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        status, payload, _headers = json_request("GET", base_url, f"/api/v1/jobs/{job_id}", cookie=cookie)
        assert_status(status, 200, "job poll")
        last_payload = payload
        if payload.get("status") in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.25)
    raise TimeoutError(f"job did not finish: {last_payload}")


def wait_for_job_status(
    base_url: str,
    job_id: str,
    cookie: str,
    accepted: set[str],
    timeout: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last_job: dict | None = None
    while time.monotonic() < deadline:
        status, payload, _headers = json_request("GET", base_url, f"/api/v1/jobs/{job_id}", cookie=cookie)
        assert_status(status, 200, "job state poll")
        last_job = payload
        if payload.get("status") in accepted:
            return payload
        time.sleep(0.05)
    raise TimeoutError(f"job did not reach {sorted(accepted)}: {last_job}")


def run_smoke_check() -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with FakeOllamaServer() as ollama_url, tempfile.TemporaryDirectory(prefix="nice-assistant-smoke-") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        archive_dir = tmp_path / "archive"
        env = os.environ.copy()
        env.update(
            {
                "PORT": str(port),
                "DATA_DIR": str(data_dir),
                "ARCHIVE_DIR": str(archive_dir),
                "OLLAMA_BASE_URL": ollama_url,
                "PROVIDER_TEST_TIMEOUT_SECONDS": "1",
                "ALLOW_PUBLIC_SIGNUP": "0",
                "NICE_ASSISTANT_MASTER_KEY": "nice-assistant-smoke-test-key",
            }
        )
        stdout_path = tmp_path / "server.stdout.log"
        stderr_path = tmp_path / "server.stderr.log"
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "app.asgi"],
                cwd=repo_root,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
        try:
            wait_for_server(base_url, proc)
            status, payload, _headers = json_request("GET", base_url, "/ready")
            assert_status(status, 200, "GET /ready")
            if not payload.get("ready"):
                raise AssertionError(f"readiness did not pass: {payload}")
            status, raw, _headers = request("GET", base_url, "/")
            assert_status(status, 200, "GET /")
            if b"/app.js" not in raw:
                raise AssertionError("GET / did not return the generated browser app shell")

            credentials = {"username": "owner", "password": "pass1234"}
            status, payload, _headers = json_request("POST", base_url, "/api/v1/users", credentials)
            assert_status(status, 200, "first signup")
            user_id = payload.get("id")
            status, payload, headers = json_request("POST", base_url, "/api/v1/session", credentials)
            assert_status(status, 200, "owner login")
            cookie = (headers.get("Set-Cookie") or "").split(";", 1)[0]
            if not cookie or payload.get("user_id") != user_id:
                raise AssertionError("login did not return the expected session")
            status, payload, _headers = json_request("GET", base_url, "/api/v1/admin/observability", cookie=cookie)
            assert_status(status, 200, "admin observability")
            if "requests" not in payload or "queues" not in payload or "storage" not in payload:
                raise AssertionError("admin observability omitted required sections")

            settings = {
                "global_default_model": "smoke-model",
                "default_memory_mode": "saved",
                "stt_provider": "disabled",
                "tts_provider": "disabled",
                "tts_format": "wav",
                "openai_api_key": "sk-smoke-hardening-1234",
                "onboarding_done": True,
                "preferences": {},
            }
            status, _payload, _headers = json_request("PUT", base_url, "/api/v1/settings", settings, cookie=cookie)
            assert_status(status, 200, "settings save")
            status, payload, _headers = json_request("GET", base_url, "/api/v1/settings", cookie=cookie)
            assert_status(status, 200, "settings read")
            if payload.get("openai_api_key") != "********1234":
                raise AssertionError("settings read did not mask the OpenAI API key")

            coordination = {
                "mode": "observe",
                "reserve_vram_mb": 512,
                "max_wait_seconds": 30,
                "poll_interval_seconds": 1,
                "authorizations": [],
            }
            status, payload, _headers = json_request(
                "PUT", base_url, "/api/v1/admin/resource-coordination", coordination, cookie=cookie
            )
            assert_status(status, 200, "resource coordination save")
            if payload.get("settings", {}).get("mode") != "observe":
                raise AssertionError("resource coordination mode did not change at runtime")
            coordination["mode"] = "disabled"
            status, payload, _headers = json_request(
                "PUT", base_url, "/api/v1/admin/resource-coordination", coordination, cookie=cookie
            )
            assert_status(status, 200, "resource coordination disable")
            if payload.get("settings", {}).get("mode") != "disabled":
                raise AssertionError("resource coordination disabled mode was not restored")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/provider-checks",
                {"provider": "ollama", "settings": {}},
                cookie=cookie,
            )
            assert_status(status, 200, "provider readiness")
            if payload.get("status") != "ready":
                raise AssertionError(f"provider readiness did not return ready: {payload}")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/users",
                {"username": "second", "password": "pass1234"},
            )
            assert_status(status, 403, "second signup")
            if (payload.get("error") or {}).get("message") != "Account creation is disabled after setup.":
                raise AssertionError("second signup did not return the safe setup-disabled message")

            image_dir = data_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            image_name = f"{user_id}_smoke.png"
            image_path = image_dir / image_name
            image_path.write_bytes(b"smoke-image")
            media_id = secrets.token_hex(8)
            connection = sqlite3.connect(data_dir / "nice_assistant.db")
            try:
                connection.execute(
                    "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                    "VALUES(?,?,NULL,'image',?,?,?)",
                    (media_id, user_id, image_name, str(image_path), int(time.time())),
                )
                connection.commit()
            finally:
                connection.close()
            status, _payload, _headers = json_request("GET", base_url, f"/api/v1/media/{media_id}")
            assert_status(status, 401, "anonymous media access")
            status, raw, _headers = request("GET", base_url, f"/api/v1/media/{media_id}", cookie=cookie)
            assert_status(status, 200, "owner media access")
            if raw != b"smoke-image":
                raise AssertionError("owner media access returned unexpected bytes")
            status, _raw, _headers = request("GET", base_url, f"/api/images/{image_name}", cookie=cookie)
            assert_status(status, 404, "removed legacy media route")

            status, backup, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/admin/backups",
                {"include_media": False},
                cookie=cookie,
            )
            assert_status(status, 200, "admin backup create")
            backup_name = backup.get("name")
            status, payload, _headers = json_request(
                "POST",
                base_url,
                f"/api/v1/admin/backups/{backup_name}/verify",
                cookie=cookie,
            )
            assert_status(status, 200, "admin backup verify")
            if payload.get("database_integrity") != "ok":
                raise AssertionError("admin backup verification did not pass integrity")
            status, payload, _headers = json_request("GET", base_url, "/api/v1/admin/backups", cookie=cookie)
            assert_status(status, 200, "admin backup list")
            if backup_name not in [item.get("name") for item in payload.get("items", [])]:
                raise AssertionError("created admin backup was not visible in list")
            status, raw, _headers = request(
                "GET",
                base_url,
                f"/api/v1/admin/backups/{backup_name}/download",
                cookie=cookie,
            )
            assert_status(status, 200, "admin backup download")
            with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
                names = set(archive.namelist())
                if {"manifest.json", "nice_assistant.db"} - names:
                    raise AssertionError(f"admin backup missing core files: {sorted(names)}")
                if f"data/images/{image_name}" in names:
                    raise AssertionError("metadata-only backup unexpectedly included media")
            status, payload, _headers = json_request(
                "DELETE",
                base_url,
                f"/api/v1/admin/backups/{backup_name}",
                cookie=cookie,
            )
            assert_status(status, 200, "admin backup delete")
            if payload.get("ok") is not True:
                raise AssertionError("admin backup delete did not return ok=true")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/media/image-jobs",
                {"prompt": "draw a smoke-test image"},
                cookie=cookie,
            )
            assert_status(status, 202, "image job start")
            image_job = wait_for_job(base_url, payload["job_id"], cookie)
            if image_job.get("status") != "failed" or "disabled" not in image_job.get("error", "").lower():
                raise AssertionError(f"disabled-provider image job did not fail honestly: {image_job}")
            status, capability, _headers = json_request(
                "GET",
                base_url,
                f"/api/v1/capability-requests/{payload['capability_request_id']}",
                cookie=cookie,
            )
            assert_status(status, 200, "disabled-provider capability status")
            if capability.get("status") != "failed" or capability.get("job_id") != payload["job_id"]:
                raise AssertionError(f"disabled-provider capability did not match its job: {capability}")

            status, workspace, _headers = json_request(
                "POST", base_url, "/api/v1/workspaces", {"name": "Smoke Workspace"}, cookie=cookie
            )
            assert_status(status, 200, "workspace create")
            status, persona, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/personas",
                {"workspace_id": workspace["id"], "name": "Smoke Persona", "system_prompt": "Be concise."},
                cookie=cookie,
            )
            assert_status(status, 200, "persona create")
            status, chat, _headers = json_request(
                "POST",
                base_url,
                "/api/v1/chats",
                {
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "memory_mode": "saved",
                    "title": "Smoke chat",
                },
                cookie=cookie,
            )
            assert_status(status, 200, "chat create")
            status, accepted, _headers = json_request(
                "POST",
                base_url,
                f"/api/v1/chats/{chat['id']}/turns",
                {"text": "say hello from the smoke test"},
                cookie=cookie,
            )
            assert_status(status, 202, "turn start")
            first_job = wait_for_job(base_url, accepted["job"]["id"], cookie)
            if first_job.get("result", {}).get("text") != "Smoke model reply.":
                raise AssertionError(f"turn did not use streamed Ollama output: {first_job}")
            extraction_job_id = first_job.get("result", {}).get("memory_extraction_job_id")
            if not extraction_job_id or wait_for_job(base_url, extraction_job_id, cookie).get("status") != "completed":
                raise AssertionError("turn did not finish durable memory candidate extraction")
            status, detail, _headers = json_request("GET", base_url, f"/api/v1/chats/{chat['id']}", cookie=cookie)
            assert_status(status, 200, "chat detail")
            if [message.get("role") for message in detail.get("messages", [])] != ["user", "assistant"]:
                raise AssertionError("turn did not persist exactly one user and assistant message")

            status, running_turn, _headers = json_request(
                "POST",
                base_url,
                f"/api/v1/chats/{chat['id']}/turns",
                {"text": "hold cancellation"},
                cookie=cookie,
            )
            assert_status(status, 202, "running cancellation setup")
            running_job_id = running_turn["job"]["id"]
            wait_for_job_status(base_url, running_job_id, cookie, {"running"})
            status, queued_turn, _headers = json_request(
                "POST",
                base_url,
                f"/api/v1/chats/{chat['id']}/turns",
                {"text": "queued cancellation"},
                cookie=cookie,
            )
            assert_status(status, 202, "queued cancellation setup")
            queued_job_id = queued_turn["job"]["id"]
            status, cancelled, _headers = json_request(
                "DELETE", base_url, f"/api/v1/jobs/{queued_job_id}", cookie=cookie
            )
            assert_status(status, 200, "queued job cancellation")
            if (
                cancelled.get("status") != "cancelled"
                or wait_for_job(base_url, queued_job_id, cookie)["status"] != "cancelled"
            ):
                raise AssertionError("queued job cancellation was not durable")
            status, cancelled, _headers = json_request(
                "DELETE", base_url, f"/api/v1/jobs/{running_job_id}", cookie=cookie
            )
            assert_status(status, 200, "running job cancellation")
            if (
                cancelled.get("status") != "cancelled"
                or wait_for_job(base_url, running_job_id, cookie)["status"] != "cancelled"
            ):
                raise AssertionError("running job cancellation was not durable")

            return {
                "health_and_generated_browser": "ok",
                "readiness_and_observability": "ok",
                "canonical_session_settings": "ok",
                "resource_coordination_policy": "ok",
                "provider_readiness": "ok",
                "blocked_second_signup": "ok",
                "protected_media_and_legacy_removal": "ok",
                "admin_backup_and_restore_drill": "ok",
                "media_job": "ok",
                "workspace_persona_chat": "ok",
                "streamed_turn_and_memory_extraction": "ok",
                "queued_and_running_cancellation": "ok",
            }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fake-ollama-port",
        type=int,
        help="serve the deterministic fake Ollama until the process is stopped",
    )
    args = parser.parse_args()
    if args.fake_ollama_port is not None:
        if not 1 <= args.fake_ollama_port <= 65535:
            parser.error("--fake-ollama-port must be between 1 and 65535")
        return run_fake_ollama_server(args.fake_ollama_port)
    result = run_smoke_check()
    print(json.dumps({"ok": True, "checks": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
