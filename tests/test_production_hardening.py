import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.asgi import create_app
from app.provider_registry import ProviderRegistry
from app.runtime import AppConfig
from app.secret_store import SecretStore
from app.security import LoginThrottle, ProviderUrlPolicy
from app.storage import write_artifact_atomic
from app.service_errors import InvalidArtifactError, RateLimitError, StorageCapacityError
from tests.support import FakeChatProvider, TestApp, fast_hash, fast_verify


CSRF = {"X-Nice-Assistant-CSRF": "1"}


def build_app(base: Path, **overrides):
    config = AppConfig(data_dir=base / "data", archive_dir=base / "archive", allow_public_signup=True, **overrides)
    return create_app(
        config,
        secret_store=SecretStore("hardening-test-key"),
        providers=ProviderRegistry(chat_providers={"ollama": FakeChatProvider()}),
        password_hasher=fast_hash,
        password_verifier=fast_verify,
    )


class ProductionHardeningTests(unittest.TestCase):
    def test_csrf_origin_and_security_headers_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            missing = running.client.post(
                "/api/v1/users",
                json={"username": "owner", "password": "pass1234"},
                headers={"X-Nice-Assistant-CSRF": ""},
            )
            self.assertEqual(missing.status_code, 403)
            self.assertEqual(missing.json()["error"]["code"], "csrf_rejected")
            hostile = running.client.post(
                "/api/v1/users",
                json={"username": "owner", "password": "pass1234"},
                headers={**CSRF, "Origin": "https://evil.example"},
            )
            self.assertEqual(hostile.status_code, 403)
            allowed = running.client.post(
                "/api/v1/users",
                json={"username": "owner", "password": "pass1234"},
                headers={**CSRF, "Origin": "http://testserver", "X-Request-ID": "request.test-123"},
            )
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.headers["x-request-id"], "request.test-123")
            self.assertEqual(allowed.headers["x-frame-options"], "DENY")
            self.assertEqual(allowed.headers["cache-control"], "no-store")

    def test_configured_reverse_proxy_origin_and_secure_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(
                Path(tmp),
                secure_cookies=True,
                allowed_origins=("https://nice.home.example",),
            )
            with TestClient(app) as client:
                headers = {**CSRF, "Origin": "https://nice.home.example"}
                created = client.post(
                    "/api/v1/users",
                    json={"username": "owner", "password": "pass1234"},
                    headers=headers,
                )
                self.assertEqual(created.status_code, 200)
                login = client.post(
                    "/api/v1/session",
                    json={"username": "owner", "password": "pass1234"},
                    headers=headers,
                )
                cookie = login.headers["set-cookie"]
                self.assertIn("Secure", cookie)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=strict", cookie)

    def test_login_throttle_is_keyed_and_reports_retry_after(self):
        now = [100.0]
        throttle = LoginThrottle(max_attempts=2, window_seconds=60, lockout_seconds=30, clock=lambda: now[0])
        key = throttle.key("127.0.0.1", "owner")
        throttle.failure(key)
        throttle.check(key)
        throttle.failure(key)
        with self.assertRaises(RateLimitError) as caught:
            throttle.check(key)
        self.assertEqual(caught.exception.retry_after, 30)
        now[0] += 31
        throttle.check(key)

    def test_login_route_locks_after_repeated_failures_without_username_leakage(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            credentials = {"username": "owner", "password": "pass1234"}
            self.assertEqual(running.client.post("/api/v1/users", json=credentials).status_code, 200)
            wrong = {**credentials, "password": "wrongpass"}
            for _ in range(5):
                response = running.client.post("/api/v1/session", json=wrong)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["message"], "invalid credentials")
            blocked = running.client.post("/api/v1/session", json=credentials)
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers["retry-after"], "900")

    def test_provider_url_policy_blocks_public_and_metadata_targets(self):
        policy = ProviderUrlPolicy()
        self.assertEqual(policy.normalize("http://100.64.0.10:8880"), "http://100.64.0.10:8880")
        self.assertEqual(policy.normalize("http://kokoro:8880"), "http://kokoro:8880")
        for target in (
            "http://8.8.8.8",
            "http://169.254.169.254/latest/meta-data",
            "http://192.0.2.1",
            "http://0.0.0.0",
            "file:///etc/passwd",
        ):
            with self.assertRaises(ValueError, msg=target):
                policy.normalize(target)
        allowed = ProviderUrlPolicy(("speech.example.net",))
        self.assertEqual(allowed.normalize("https://speech.example.net/v1"), "https://speech.example.net/v1")

    def test_user_configured_and_per_request_provider_urls_use_the_policy(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            settings = running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_local_base_url": "http://169.254.169.254"}},
            )
            self.assertEqual(settings.status_code, 400)
            explicit = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "test", "provider": "local", "base_url": "http://8.8.8.8"},
            )
            self.assertEqual(explicit.status_code, 400)

    def test_readiness_observability_admin_scope_and_storage_reporting(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login("owner")
            ready = running.client.get("/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertTrue(ready.json()["ready"])
            running.client.put(
                "/api/v1/settings",
                json={"tts_provider": "openai", "openai_api_key": "sk-observability-test", "preferences": {}},
            )
            with mock.patch("app.speech_service.openai_speech", return_value=b"audio"):
                self.assertEqual(
                    running.client.post("/api/v1/speech/syntheses", json={"text": "metric"}).status_code,
                    200,
                )
            report = running.client.get("/api/v1/admin/observability")
            self.assertEqual(report.status_code, 200)
            self.assertIn("requests", report.json())
            self.assertIn("queues", report.json())
            self.assertEqual(report.json()["providers"]["counts"]["openai:speech:completed"], 1)
            self.assertIn("retention", report.json()["storage"])
            running.create_and_login("member")
            self.assertEqual(running.client.get("/api/v1/admin/observability").status_code, 403)

    def test_retention_prunes_only_expired_configured_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(
                Path(tmp),
                audio_archive_retention_days=1,
                stt_recording_retention_days=1,
                log_archive_retention_days=1,
            )
            with TestClient(app) as client:
                client.headers.update(CSRF)
                services = app.state.services
                old_audio = services.runtime.config.archive_dir / "audio" / "old.wav"
                recent_audio = services.runtime.config.archive_dir / "audio" / "recent.wav"
                old_audio.write_bytes(b"old")
                recent_audio.write_bytes(b"recent")
                old_stamp = time.time() - 2 * 86400
                os.utime(old_audio, (old_stamp, old_stamp))
                services.operations.startup_maintenance()
                self.assertFalse(old_audio.exists())
                self.assertTrue(recent_audio.exists())

    def test_audio_rotation_updates_the_protected_replay_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(Path(tmp), audio_hot_limit=1)
            with TestClient(app) as client:
                client.headers.update(CSRF)
                credentials = {"username": "owner", "password": "pass1234"}
                self.assertEqual(client.post("/api/v1/users", json=credentials).status_code, 200)
                self.assertEqual(client.post("/api/v1/session", json=credentials).status_code, 200)
                settings = client.put(
                    "/api/v1/settings",
                    json={
                        "tts_provider": "openai",
                        "openai_api_key": "sk-rotation-test",
                        "preferences": {},
                    },
                )
                self.assertEqual(settings.status_code, 200, settings.text)
                with mock.patch("app.speech_service.openai_speech", return_value=b"audio"):
                    first = client.post("/api/v1/speech/syntheses", json={"text": "first"})
                    second = client.post("/api/v1/speech/syntheses", json={"text": "second"})
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertEqual(client.get(first.json()["audio_url"]).content, b"audio")
                self.assertEqual(client.get(second.json()["audio_url"]).content, b"audio")
                self.assertEqual(len(list((app.state.services.runtime.config.archive_dir / "audio").iterdir())), 1)

    def test_backup_restore_drill_and_corrupt_snapshot_failure_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            created = running.client.post("/api/v1/admin/backups", json={"include_media": False})
            self.assertEqual(created.status_code, 200, created.text)
            verified = running.client.post(f"/api/v1/admin/backups/{created.json()['name']}/verify")
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["database_integrity"], "ok")
            corrupt_name = "nice-assistant-snapshot-20260714_120000-deadbeef.zip"
            (running.config.backup_dir / corrupt_name).write_bytes(b"not a zip")
            corrupt = running.client.post(f"/api/v1/admin/backups/{corrupt_name}/verify")
            self.assertEqual(corrupt.status_code, 409)
            self.assertNotIn(str(running.config.backup_dir), corrupt.text)

    def test_atomic_artifacts_reject_empty_content_and_disk_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact.bin"
            with self.assertRaises(InvalidArtifactError):
                write_artifact_atomic(target, b"")
            with mock.patch("app.storage.os.replace", side_effect=OSError("disk full secret path")):
                with self.assertRaises(StorageCapacityError) as caught:
                    write_artifact_atomic(target, b"content")
            self.assertEqual(caught.exception.code, "storage_unavailable")
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
