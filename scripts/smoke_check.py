#!/usr/bin/env python3
"""Process-level smoke check for the Nice Assistant Python server."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(method: str, base_url: str, path: str, body: dict | None = None, cookie: str | None = None):
    data = None
    headers = {}
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


def assert_status(actual: int, expected: int, label: str):
    if actual != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, got {actual}")


def wait_for_job(base_url: str, job_id: str, cookie: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        status, payload, _headers = json_request("GET", base_url, f"/api/jobs/{job_id}", cookie=cookie)
        assert_status(status, 200, "job poll")
        last_payload = payload
        job = payload.get("job") or {}
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise TimeoutError(f"job did not finish: {last_payload}")


def run_smoke_check() -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="nice-assistant-smoke-") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        archive_dir = tmp_path / "archive"
        stdout_path = tmp_path / "server.stdout.log"
        stderr_path = tmp_path / "server.stderr.log"
        env = os.environ.copy()
        env.update(
            {
                "PORT": str(port),
                "DATA_DIR": str(data_dir),
                "ARCHIVE_DIR": str(archive_dir),
                "OLLAMA_BASE_URL": "http://127.0.0.1:9",
                "PROVIDER_TEST_TIMEOUT_SECONDS": "1",
                "ALLOW_PUBLIC_SIGNUP": "0",
            }
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "app.server"],
            cwd=repo_root,
            env=env,
            stdout=stdout_path.open("wb"),
            stderr=stderr_path.open("wb"),
        )
        try:
            wait_for_server(base_url, proc)

            status, raw, _headers = request("GET", base_url, "/")
            assert_status(status, 200, "GET /")
            if b"/app.js" not in raw:
                raise AssertionError("GET / did not return the browser app shell")

            owner_body = {"username": "owner", "password": "pass1234"}
            status, payload, _headers = json_request("POST", base_url, "/api/users", owner_body)
            assert_status(status, 200, "first signup")
            if not payload.get("ok"):
                raise AssertionError("first signup did not return ok=true")

            status, payload, headers = json_request("POST", base_url, "/api/login", owner_body)
            assert_status(status, 200, "owner login")
            cookie = (headers.get("Set-Cookie") or "").split(";", 1)[0]
            user_id = payload.get("userId")
            if not cookie or not user_id:
                raise AssertionError("login did not return a session cookie and user id")

            settings_payload = {
                "global_default_model": "",
                "default_memory_mode": "auto",
                "stt_provider": "disabled",
                "tts_provider": "disabled",
                "tts_format": "wav",
                "openai_api_key": "sk-smoke-hardening-1234",
                "onboarding_done": 0,
                "preferences_json": "{}",
            }
            status, _payload, _headers = json_request("POST", base_url, "/api/settings", settings_payload, cookie=cookie)
            assert_status(status, 200, "settings save")
            status, payload, _headers = json_request("GET", base_url, "/api/settings", cookie=cookie)
            assert_status(status, 200, "settings read")
            if payload.get("settings", {}).get("openai_api_key") != "********1234":
                raise AssertionError("settings read did not mask the OpenAI API key")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/providers/test",
                {"provider": "ollama", "settings": settings_payload},
                cookie=cookie,
            )
            assert_status(status, 200, "provider readiness test")
            if payload.get("ok") is not False or payload.get("status") not in {"unreachable", "failed", "error"}:
                raise AssertionError(f"provider readiness test did not return a safe unreachable result: {payload}")
            if "ollama" not in (payload.get("provider") or "").lower():
                raise AssertionError("provider readiness test did not identify the provider")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/users",
                {"username": "second", "password": "pass1234"},
            )
            assert_status(status, 403, "second signup")
            if payload.get("error") != "Account creation is disabled after setup.":
                raise AssertionError("second signup did not return the setup-disabled message")

            image_dir = data_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            image_name = f"{user_id}_smoke.png"
            image_path = image_dir / image_name
            image_path.write_bytes(b"smoke-image")

            status, payload, _headers = json_request("GET", base_url, f"/api/images/{image_name}")
            assert_status(status, 401, "anonymous image access")
            if payload.get("error") != "unauthorized":
                raise AssertionError("anonymous image access did not return unauthorized")

            status, raw, _headers = request("GET", base_url, f"/api/images/{image_name}", cookie=cookie)
            assert_status(status, 200, "owner image access")
            if raw != b"smoke-image":
                raise AssertionError("owner image access returned unexpected bytes")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/admin/backups",
                {"includeMedia": False},
                cookie=cookie,
            )
            assert_status(status, 200, "admin backup create")
            backup = payload.get("backup") or {}
            backup_name = backup.get("name")
            if not backup_name:
                raise AssertionError("admin backup create did not return a backup name")

            status, payload, _headers = json_request("GET", base_url, "/api/admin/backups", cookie=cookie)
            assert_status(status, 200, "admin backup list")
            if backup_name not in [item.get("name") for item in payload.get("items", [])]:
                raise AssertionError("created admin backup was not visible in list")

            status, raw, _headers = request("GET", base_url, f"/api/admin/backups/{backup_name}/download", cookie=cookie)
            assert_status(status, 200, "admin backup download")
            with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
                names = set(zf.namelist())
                if "manifest.json" not in names or "nice_assistant.db" not in names:
                    raise AssertionError(f"admin backup missing core files: {sorted(names)}")
                if f"data/images/{image_name}" in names:
                    raise AssertionError("metadata-only backup unexpectedly included media")

            status, payload, _headers = json_request("DELETE", base_url, f"/api/admin/backups/{backup_name}", cookie=cookie)
            assert_status(status, 200, "admin backup delete")
            if payload.get("ok") is not True:
                raise AssertionError("admin backup delete did not return ok=true")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/images/generate",
                {"prompt": "draw a smoke-test image", "async": True},
                cookie=cookie,
            )
            assert_status(status, 202, "async image start")
            job_id = payload.get("jobId")
            if not job_id:
                raise AssertionError("async image start did not return a job id")
            image_job = wait_for_job(base_url, job_id, cookie)
            if image_job.get("status") != "completed":
                raise AssertionError(f"async image job did not complete: {image_job}")
            if image_job.get("result", {}).get("ok") is not False:
                raise AssertionError("disabled-provider async image job did not expose an ok=false result")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/workspaces",
                {"name": "Smoke Workspace"},
                cookie=cookie,
            )
            assert_status(status, 200, "workspace create")
            workspace_id = payload.get("id")
            if not workspace_id:
                raise AssertionError("workspace create did not return an id")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/personas",
                {"workspaceId": workspace_id, "name": "Smoke Persona", "systemPrompt": "Be concise."},
                cookie=cookie,
            )
            assert_status(status, 200, "persona create")
            persona_id = payload.get("id")
            if not persona_id:
                raise AssertionError("persona create did not return an id")

            status, payload, _headers = json_request("GET", base_url, "/api/personas", cookie=cookie)
            assert_status(status, 200, "persona list")
            if persona_id not in [item.get("id") for item in payload.get("items", [])]:
                raise AssertionError("created persona was not visible in persona list")

            status, payload, _headers = json_request(
                "POST",
                base_url,
                "/api/chat",
                {
                    "text": "generate image of a smoke-test cat",
                    "workspaceId": workspace_id,
                    "personaId": persona_id,
                    "async": True,
                },
                cookie=cookie,
            )
            assert_status(status, 202, "async first chat start")
            first_chat_job_id = payload.get("jobId")
            if not first_chat_job_id:
                raise AssertionError("async first chat start did not return a job id")
            first_chat_job = wait_for_job(base_url, first_chat_job_id, cookie)
            if first_chat_job.get("status") != "completed":
                raise AssertionError(f"async first chat did not complete: {first_chat_job}")
            chat_id = first_chat_job.get("result", {}).get("chatId")
            if not chat_id:
                raise AssertionError("async first chat did not return a chat id")
            status, payload, _headers = json_request("GET", base_url, f"/api/chats/{chat_id}", cookie=cookie)
            assert_status(status, 200, "first chat read")
            messages = payload.get("messages", [])
            roles = [message.get("role") for message in messages]
            if roles != ["user", "assistant"]:
                raise AssertionError(f"first chat did not persist user and assistant messages: {roles}")

            return {
                "health": "ok",
                "index": "ok",
                "signup": "ok",
                "login": "ok",
                "settings_mask": "ok",
                "provider_readiness": "ok",
                "blocked_second_signup": "ok",
                "protected_media": "ok",
                "admin_backup": "ok",
                "async_image_job": "ok",
                "workspace_persona_setup": "ok",
                "async_first_chat": "ok",
            }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> int:
    result = run_smoke_check()
    print(json.dumps({"ok": True, "checks": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
