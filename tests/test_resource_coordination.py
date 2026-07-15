import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from pathlib import Path

from app.job_service import JobExecution
from app.provider_contracts import (
    CapacityStatus,
    ProviderCapacitySnapshot,
    ProviderRuntimeCapabilities,
)
from app.repositories import UnitOfWork
from app.resource_coordination import ResourceRequest
from tests.support import TestApp


class FakeResourceProvider:
    def __init__(self, name: str, *, free_vram_mb: int | None = 4096, release_free_vram_mb: int | None = None):
        self.name = name
        self.free_vram_mb = free_vram_mb
        self.release_free_vram_mb = release_free_vram_mb
        self.release_calls = 0
        self.snapshot_calls = 0
        self.release_started = threading.Event()
        self.release_gate: threading.Event | None = None
        self._lock = threading.Lock()

    def capabilities(self, _endpoint, _api_auth=None):
        return ProviderRuntimeCapabilities(
            self.name,
            reports_capacity=self.name != "ollama",
            reports_queue=self.name == "comfyui",
            supports_release=True,
        )

    def snapshot(self, _endpoint, _api_auth=None):
        with self._lock:
            self.snapshot_calls += 1
            free = self.free_vram_mb
        if self.name == "ollama":
            return ProviderCapacitySnapshot(
                self.name,
                CapacityStatus.UNKNOWN,
                "/api/ps",
                loaded_models=({"name": "fake-model", "vram_mb": 1024},),
            )
        status = CapacityStatus.KNOWN if free is not None else CapacityStatus.UNAVAILABLE
        return ProviderCapacitySnapshot(
            self.name,
            status,
            "/fake-capacity",
            total_vram_mb=12288 if free is not None else None,
            free_vram_mb=free,
            queue_depth=0,
            active_jobs=0,
            message="" if free is not None else "Fake telemetry unavailable.",
        )

    def release(self, _endpoint, _api_auth=None):
        with self._lock:
            self.release_calls += 1
            if self.release_free_vram_mb is not None:
                self.free_vram_mb = self.release_free_vram_mb
        self.release_started.set()
        if self.release_gate:
            self.release_gate.wait(timeout=3)
        return {"requested": True, "scope": "fake", "model_count": 1}


def provider_set(*, comfy_free=4096, comfy_release=None):
    return {
        "ollama": FakeResourceProvider("ollama"),
        "comfyui": FakeResourceProvider("comfyui", free_vram_mb=comfy_free, release_free_vram_mb=comfy_release),
        "automatic1111": FakeResourceProvider("automatic1111"),
    }


class ResourceCoordinationTests(unittest.TestCase):
    def _save_policy(self, app, *, mode="observe", wait=3, authorize=False):
        providers = ["ollama", "comfyui", "automatic1111"]
        response = app.client.put(
            "/api/v1/admin/resource-coordination",
            json={
                "mode": mode,
                "reserve_vram_mb": 0,
                "max_wait_seconds": wait,
                "poll_interval_seconds": 0.25,
                "authorizations": [
                    {
                        "provider": provider,
                        "exclusive_control": authorize,
                        "allow_release": authorize,
                    }
                    for provider in providers
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _submit(app, user_id, request, *, value="done", started=None):
        with UnitOfWork(app.services.runtime.session_factory, app.services.runtime.secret_store) as uow:
            job = uow.repo.add_job(
                user_id=user_id,
                chat_id=None,
                turn_id=None,
                kind="image",
                progress="Queued",
            )
            job_id = job.id
        app.services.jobs.submit(
            job_id=job_id,
            job_type="image",
            user_id=user_id,
            chat_id=None,
            turn_id=None,
            latency_class="standard",
            model_key="image:test",
            execution=JobExecution(execute=lambda _token: (started.set() if started else None, {"value": value})[-1]),
            estimated_vram_mb=request.estimated_vram_mb,
            resource_request=request,
        )
        return job_id

    def test_admin_api_reports_sources_and_rejects_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set()
            with TestApp(Path(tmp), resource_providers=resources) as app:
                app.create_and_login("owner")
                response = app.client.get("/api/v1/admin/resource-coordination")
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["settings"]["mode"], "disabled")
                self.assertEqual({item["provider"] for item in body["endpoints"]}, set(resources))
                self.assertTrue(all(item["snapshot"] for item in body["endpoints"]))
                app.create_and_login("member")
                denied = app.client.get("/api/v1/admin/resource-coordination")
                self.assertEqual(denied.status_code, 403)

    def test_observe_mode_waits_without_consuming_worker_then_admits(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="observe")
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 1000)
                job_id = self._submit(app, user_id, request)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    current = app.services.jobs.get(user_id, job_id)
                    if current["progress"] == "Waiting for GPU capacity":
                        break
                    time.sleep(0.01)
                self.assertEqual(current["status"], "queued")
                self.assertEqual(current["progress"], "Waiting for GPU capacity")
                resources["comfyui"].free_vram_mb = 4096
                result = app.services.jobs.wait(user_id, job_id, timeout=4)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["result"], {"value": "done"})
                actions = [item["action"] for item in app.services.resource_coordination.events()]
                self.assertIn("waiting", actions)
                self.assertIn("admitted", actions)
                self.assertEqual(resources["comfyui"].release_calls, 0)

    def test_waiting_media_yields_to_chat_and_cannot_start_during_chat_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="observe")
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 1000)
                media_started = threading.Event()
                media_id = self._submit(app, user_id, request, value="media", started=media_started)
                with UnitOfWork(app.services.runtime.session_factory, app.services.runtime.secret_store) as uow:
                    chat_row = uow.repo.add_job(
                        user_id=user_id,
                        chat_id=None,
                        turn_id=None,
                        kind="chat",
                        progress="Queued",
                    )
                    chat_id = chat_row.id
                chat_started = threading.Event()
                release_chat = threading.Event()
                app.services.jobs.submit(
                    job_id=chat_id,
                    job_type="chat",
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    latency_class="interactive",
                    model_key="chat:test",
                    execution=JobExecution(
                        execute=lambda _token: (chat_started.set(), release_chat.wait(timeout=3), {"value": "chat"})[-1]
                    ),
                )
                self.assertTrue(chat_started.wait(timeout=1))
                resources["comfyui"].free_vram_mb = 4096
                time.sleep(0.4)
                self.assertFalse(media_started.is_set())
                self.assertEqual(app.services.jobs.get(user_id, media_id)["status"], "queued")
                release_chat.set()
                self.assertEqual(app.services.jobs.wait(user_id, chat_id, timeout=2)["status"], "completed")
                self.assertEqual(app.services.jobs.wait(user_id, media_id, timeout=3)["status"], "completed")

    def test_managed_mode_releases_only_authorized_endpoints_and_verifies_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0, comfy_release=4096)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 1000)
                job_id = self._submit(app, user_id, request)
                result = app.services.jobs.wait(user_id, job_id, timeout=4)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(resources["comfyui"].release_calls, 2)
                self.assertEqual(resources["ollama"].release_calls, 1)
                events = app.services.resource_coordination.events()
                release_triggers = {item["detail"].get("trigger") for item in events if item["action"] == "released"}
                self.assertEqual(release_triggers, {"pre_admission", "post_job"})
                admitted = next(item for item in events if item["action"] == "admitted")
                self.assertEqual(admitted["detail"]["free_vram_mb"], 4096)

    def test_managed_mode_reclaims_unknown_demand_after_job_without_false_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                job_id = self._submit(app, user_id, request)
                result = app.services.jobs.wait(user_id, job_id, timeout=4)

                self.assertEqual(result["status"], "completed")
                self.assertEqual(resources["comfyui"].snapshot_calls, 0)
                self.assertEqual(resources["comfyui"].release_calls, 1)
                self.assertEqual(resources["ollama"].release_calls, 0)
                events = app.services.resource_coordination.events()
                self.assertFalse(any(item["action"] == "admitted" for item in events))
                released = next(item for item in events if item["action"] == "released")
                self.assertEqual(released["detail"]["trigger"], "post_job")

    def test_observe_mode_tracks_unknown_demand_without_releasing_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="observe", authorize=True)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                job_id = self._submit(app, user_id, request)
                result = app.services.jobs.wait(user_id, job_id, timeout=4)

                self.assertEqual(result["status"], "completed")
                self.assertEqual(resources["comfyui"].release_calls, 0)
                self.assertEqual(resources["ollama"].release_calls, 0)
                self.assertEqual(app.services.resource_coordination.events(), [])

    def test_post_job_release_holds_lease_until_cleanup_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            resources["comfyui"].release_gate = threading.Event()
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                media_id = self._submit(app, user_id, request)
                self.assertTrue(resources["comfyui"].release_started.wait(timeout=2))

                with UnitOfWork(app.services.runtime.session_factory, app.services.runtime.secret_store) as uow:
                    chat_row = uow.repo.add_job(
                        user_id=user_id,
                        chat_id=None,
                        turn_id=None,
                        kind="chat",
                        progress="Queued",
                    )
                    chat_id = chat_row.id
                chat_started = threading.Event()
                app.services.jobs.submit(
                    job_id=chat_id,
                    job_type="chat",
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    latency_class="interactive",
                    model_key="chat:test",
                    execution=JobExecution(execute=lambda _token: (chat_started.set(), {"value": "chat"})[-1]),
                )
                time.sleep(0.2)
                self.assertFalse(chat_started.is_set())
                self.assertEqual(app.services.jobs.get(user_id, chat_id)["status"], "queued")

                resources["comfyui"].release_gate.set()
                self.assertEqual(app.services.jobs.wait(user_id, media_id, timeout=3)["status"], "completed")
                self.assertEqual(app.services.jobs.wait(user_id, chat_id, timeout=3)["status"], "completed")

    def test_cancelled_unknown_demand_that_never_started_does_not_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                with UnitOfWork(app.services.runtime.session_factory, app.services.runtime.secret_store) as uow:
                    chat_row = uow.repo.add_job(
                        user_id=user_id,
                        chat_id=None,
                        turn_id=None,
                        kind="chat",
                        progress="Queued",
                    )
                    chat_id = chat_row.id
                chat_started = threading.Event()
                release_chat = threading.Event()
                app.services.jobs.submit(
                    job_id=chat_id,
                    job_type="chat",
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    latency_class="interactive",
                    model_key="chat:test",
                    execution=JobExecution(
                        execute=lambda _token: (chat_started.set(), release_chat.wait(timeout=3), {"value": "chat"})[-1]
                    ),
                )
                self.assertTrue(chat_started.wait(timeout=1))

                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                media_id = self._submit(app, user_id, request)
                cancelled = app.services.jobs.cancel(user_id, media_id)
                self.assertEqual(cancelled["status"], "cancelled")
                release_chat.set()
                self.assertEqual(app.services.jobs.wait(user_id, chat_id, timeout=3)["status"], "completed")
                self.assertEqual(resources["comfyui"].release_calls, 0)

    def test_selected_job_that_never_begins_does_not_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                coordinator = app.services.resource_coordination
                coordinator.register("never-began", request)
                coordinator.reserve(
                    SimpleNamespace(
                        id="queue-never-began",
                        metadata={"coordinated_resource": True, "async_job_id": "never-began"},
                    )
                )
                coordinator.complete("queue-never-began", "never-began")

                self.assertEqual(resources["comfyui"].release_calls, 0)
                self.assertEqual(coordinator.events(), [])

    def test_running_cancellation_keeps_lifecycle_record_for_post_job_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=0)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", authorize=True)
                with UnitOfWork(app.services.runtime.session_factory, app.services.runtime.secret_store) as uow:
                    row = uow.repo.add_job(
                        user_id=user_id,
                        chat_id=None,
                        turn_id=None,
                        kind="image",
                        progress="Queued",
                    )
                    job_id = row.id
                execution_started = threading.Event()
                finish_provider = threading.Event()
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 0)
                app.services.jobs.submit(
                    job_id=job_id,
                    job_type="image",
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    latency_class="standard",
                    model_key="image:test",
                    execution=JobExecution(
                        execute=lambda _token: (
                            execution_started.set(),
                            finish_provider.wait(timeout=3),
                            {"value": "late"},
                        )[-1]
                    ),
                    resource_request=request,
                )
                self.assertTrue(execution_started.wait(timeout=1))
                first = app.services.jobs.cancel(user_id, job_id)
                second = app.services.jobs.cancel(user_id, job_id)
                self.assertEqual(first["status"], "cancelled")
                self.assertEqual(second["status"], "cancelled")
                self.assertEqual(resources["comfyui"].release_calls, 0)

                finish_provider.set()
                self.assertTrue(resources["comfyui"].release_started.wait(timeout=2))
                self.assertTrue(app.services.jobs.queue.wait_until_idle(timeout=2))
                self.assertEqual(resources["comfyui"].release_calls, 1)
                cancelled_events = [
                    item for item in app.services.resource_coordination.events() if item["action"] == "cancelled"
                ]
                self.assertEqual(len(cancelled_events), 1)

    def test_unavailable_capacity_times_out_safely_and_cancellation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set(comfy_free=None)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                self._save_policy(app, mode="managed", wait=1, authorize=False)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, None, 1000)
                failed_id = self._submit(app, user_id, request)
                failed = app.services.jobs.wait(user_id, failed_id, timeout=4)
                self.assertEqual(failed["status"], "failed")
                self.assertNotIn("http", failed["error"].lower())
                self.assertEqual(resources["comfyui"].release_calls, 0)
                cancel_id = self._submit(app, user_id, request)
                first = app.services.jobs.cancel(user_id, cancel_id)
                second = app.services.jobs.cancel(user_id, cancel_id)
                self.assertEqual(first["status"], "cancelled")
                self.assertEqual(second["status"], "cancelled")

    def test_endpoint_change_invalidates_release_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = provider_set()
            with TestApp(Path(tmp), resource_providers=resources) as app:
                app.create_and_login()
                saved = self._save_policy(app, mode="managed", authorize=True)
                original = next(item for item in saved["endpoints"] if item["provider"] == "comfyui")
                self.assertTrue(original["authorization"]["allow_release"])
                settings = app.client.get("/api/v1/settings").json()
                settings["preferences"]["image_local_backend"] = "comfyui"
                settings["preferences"]["image_local_base_url"] = "http://127.0.0.1:9199"
                updated = app.client.put("/api/v1/settings", json=settings)
                self.assertEqual(updated.status_code, 200, updated.text)
                status = app.services.resource_coordination.status(
                    app.client.get("/api/v1/session").json()["user_id"], refresh=False
                )
                changed = next(item for item in status["endpoints"] if item["provider"] == "comfyui")
                self.assertNotEqual(changed["fingerprint"], original["fingerprint"])
                self.assertFalse(changed["authorization"]["allow_release"])


if __name__ == "__main__":
    unittest.main()
