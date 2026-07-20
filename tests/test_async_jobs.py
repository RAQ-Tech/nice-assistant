import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from app.job_service import JobExecution, LEGAL_TRANSITIONS, InvalidJobTransition, transition_job, transition_turn
from app.models import MediaFile
from app.provider_contracts import ChatToolCall, MediaArtifact, ProviderError
from app.repositories import UnitOfWork
from app.task_contracts import CAPABILITY_PLANNING, TITLE_GENERATION
from tests.support import FakeChatProvider, TestApp


class AsyncJobTests(unittest.TestCase):
    def test_shutdown_terminalizes_a_pending_job_accepted_just_before_the_gate_closes(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            jobs = running.services.jobs
            with jobs._uow() as uow:
                active_job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    kind="task_model",
                    progress="Queued",
                )
                pending_job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    kind="task_model",
                    progress="Queued",
                )

            active_started = threading.Event()
            pending_accepted = threading.Event()
            release_pending_submit = threading.Event()
            pending_cancel_count = 0
            original_submit = jobs.queue.submit
            stopped_queue = jobs.queue
            original_coordinator_cancel = running.services.resource_coordination.cancel
            coordinator_cancellations = []

            def active_execute(token):
                active_started.set()
                while True:
                    token.raise_if_cancelled()
                    time.sleep(0.01)

            def pending_cancel(_repo):
                nonlocal pending_cancel_count
                pending_cancel_count += 1

            def controlled_submit(queue_job):
                submitted = original_submit(queue_job)
                if queue_job.metadata.get("async_job_id") == pending_job.id:
                    pending_accepted.set()
                    self.assertTrue(release_pending_submit.wait(timeout=2))
                return submitted

            def recording_coordinator_cancel(job_id):
                coordinator_cancellations.append(job_id)
                return original_coordinator_cancel(job_id)

            jobs.queue.submit = controlled_submit
            running.services.resource_coordination.cancel = recording_coordinator_cancel
            jobs.submit(
                job_id=active_job.id,
                job_type="task_model",
                user_id=user_id,
                chat_id=None,
                turn_id=None,
                latency_class="standard",
                model_key="task:active",
                execution=JobExecution(execute=active_execute),
            )
            self.assertTrue(active_started.wait(timeout=1))

            submit_thread = threading.Thread(
                target=lambda: jobs.submit(
                    job_id=pending_job.id,
                    job_type="task_model",
                    user_id=user_id,
                    chat_id=None,
                    turn_id=None,
                    latency_class="standard",
                    model_key="task:pending",
                    execution=JobExecution(execute=lambda _token: None, on_cancel=pending_cancel),
                )
            )
            stop_thread = threading.Thread(target=jobs.stop)
            submit_thread.start()
            self.assertTrue(pending_accepted.wait(timeout=1))
            stop_thread.start()
            try:
                stop_thread.join(timeout=0.05)
                self.assertTrue(stop_thread.is_alive())
            finally:
                release_pending_submit.set()
                submit_thread.join(timeout=2)
                stop_thread.join(timeout=2)

            self.assertFalse(submit_thread.is_alive())
            self.assertFalse(stop_thread.is_alive())
            pending = running.client.get(f"/api/v1/jobs/{pending_job.id}").json()
            self.assertEqual(pending["status"], "cancelled")
            self.assertTrue(pending["cancel_requested"])
            self.assertEqual(pending_cancel_count, 1)
            self.assertEqual(coordinator_cancellations.count(pending_job.id), 1)
            self.assertNotIn(pending_job.id, jobs._tokens)
            self.assertNotIn(pending_job.id, jobs._done)
            self.assertNotIn(pending_job.id, jobs._executions)
            self.assertEqual(stopped_queue.stopped_pending_jobs(), [])
            self.assertIsNone(jobs.queue)

    def test_title_and_capability_followups_are_distinct_jobs_after_reply_delivery(self):
        provider = FakeChatProvider(
            ["The reply arrives first."],
            task_outputs={
                TITLE_GENERATION: {"title": "Independent Followups"},
                CAPABILITY_PLANNING: {"requests": []},
            },
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), chat_provider=provider, interactive_workers=2) as running,
        ):
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = object()
            running.client.put(
                "/api/v1/settings",
                json={
                    "preferences": {
                        "image_provider": "local/automatic1111",
                        "image_confirmation_policy": "always_ask",
                    }
                },
            )
            chat = running.client.post("/api/v1/chats", json={"title": "New chat", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me a garden", "memory_mode": "off"},
            ).json()
            deadline = time.monotonic() + 2
            primary = None
            while time.monotonic() < deadline:
                primary = running.client.get(f"/api/v1/jobs/{accepted['job']['id']}").json()
                if primary["status"] == "completed":
                    break
                time.sleep(0.01)

            self.assertEqual(primary["status"], "completed")
            self.assertEqual(primary["result"]["text"], "I’ll see what I can make for you.")
            self.assertEqual(len(primary["result"]["followup_job_ids"]), 2)
            self.assertNotEqual(
                primary["result"]["title_job_id"],
                primary["result"]["capability_planning_job_id"],
            )
            title_job = running.wait_job(primary["result"]["title_job_id"])
            self.assertEqual(title_job["status"], "completed")
            capability_job = running.wait_job(primary["result"]["capability_planning_job_id"])
            self.assertEqual(capability_job["status"], "completed")
            self.assertEqual(
                [provider._task_role(request) for request in provider.task_requests],
                [TITLE_GENERATION, CAPABILITY_PLANNING],
            )

    def test_persona_reply_completes_before_nonessential_capability_planning(self):
        planning_gate = threading.Event()
        provider = FakeChatProvider(
            ["The visible reply is ready."],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
            task_gates={CAPABILITY_PLANNING: planning_gate},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = object()
            running.client.put(
                "/api/v1/settings",
                json={
                    "preferences": {
                        "image_provider": "local/automatic1111",
                        "image_confirmation_policy": "always_ask",
                    }
                },
            )
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Already named", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Would you make an image of a garden?", "memory_mode": "off"},
            ).json()
            job_id = accepted["job"]["id"]
            deadline = time.monotonic() + 2
            primary = None
            while time.monotonic() < deadline:
                primary = running.client.get(f"/api/v1/jobs/{job_id}").json()
                if primary["status"] == "completed":
                    break
                time.sleep(0.01)

            self.assertEqual(primary["status"], "completed")
            self.assertEqual(primary["result"]["text"], "I’ll see what I can make for you.")
            self.assertIn("followup_job_id", primary["result"])
            self.assertTrue(provider.task_started[CAPABILITY_PLANNING].wait(1))
            followup = running.client.get(f"/api/v1/jobs/{primary['result']['followup_job_id']}").json()
            self.assertIn(followup["status"], {"queued", "running"})
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(detail["messages"][-1]["text"], "I’ll see what I can make for you.")
            planning_gate.set()
            running.wait_job(primary["result"]["followup_job_id"])

    def test_premature_persona_media_claim_is_never_streamed_or_persisted(self):
        provider = FakeChatProvider(
            ["Here is that picture for you: [Image]"],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = object()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Truthful media", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Create a portrait of the persona", "memory_mode": "off"},
            ).json()
            completed = running.wait_job(accepted["job"]["id"])
            self.assertTrue(completed["result"]["mediaClaimGuarded"])
            self.assertEqual(completed["result"]["text"], "I’ll see what I can make for you.")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(detail["messages"][-1]["text"], "I’ll see what I can make for you.")
            replay = list(
                running.services.conversations.broker.subscribe(
                    accepted["turn"]["id"],
                    {"status": "completed"},
                )
            )
            streamed = "".join(
                str(event.data.get("text") or "") for event in replay if event.event == "assistant.delta"
            )
            self.assertEqual(streamed, "I’ll see what I can make for you.")

    def test_observed_prompt_leak_and_shutter_roleplay_collapse_to_safe_image_acknowledgement(self):
        provider = FakeChatProvider(
            [
                "[SYSTEM_",
                "PROMPT]private platform policy[/SYSTEM_PROMPT]",
                "*holds up my phone and taps the shutter* Ta-da!",
            ],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            generated_requests = []
            running.services.providers.media_providers["local-image"] = SimpleNamespace(
                generate=lambda request, _cancellation: (
                    generated_requests.append(request),
                    MediaArtifact("image", b"image-bytes", ".png", "image/png"),
                )[-1]
            )
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Observed production regression", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "Candy, please send me an image of a single red apple.",
                    "memory_mode": "off",
                },
            ).json()
            completed = running.wait_job(accepted["job"]["id"])
            planning = running.wait_job(completed["result"]["capability_planning_job_id"])
            self.assertEqual(planning["status"], "completed")
            request = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            image_job = running.wait_job(request["job_id"])
            self.assertEqual(image_job["status"], "completed", image_job)

            self.assertEqual(completed["result"]["text"], "I’ll see what I can make for you.")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(detail["messages"][-1]["text"], "I’ll see what I can make for you.")
            self.assertEqual(detail["messages"][-1]["attachments"][0]["status"], "completed")
            self.assertEqual(len(generated_requests), 1)
            replay = list(
                running.services.conversations.broker.subscribe(
                    accepted["turn"]["id"],
                    {"status": "completed"},
                )
            )
            streamed = "".join(
                str(event.data.get("text") or "") for event in replay if event.event == "assistant.delta"
            )
            self.assertEqual(streamed, "I’ll see what I can make for you.")
            self.assertNotIn("private platform policy", str(completed))
            self.assertNotIn("SYSTEM_PROMPT", str(completed))

    def test_explicit_image_request_gets_a_durable_failure_when_no_image_provider_is_ready(self):
        provider = FakeChatProvider(["*taps the shutter* Ta-da!"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Unavailable image provider", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Please generate an image of a blue cup.", "memory_mode": "off"},
            ).json()
            completed = running.wait_job(accepted["job"]["id"])
            planning = running.wait_job(completed["result"]["capability_planning_job_id"])

            self.assertEqual(completed["result"]["text"], "I’ll see what I can make for you.")
            self.assertEqual(planning["status"], "completed")
            requests = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["status"], "failed")
            self.assertEqual(requests[0]["attachment"]["status"], "failed")
            self.assertIsNone(requests[0]["job_id"])
            self.assertEqual(provider.task_requests, [])

    def test_prompt_envelope_is_removed_before_streaming_and_persistence(self):
        provider = FakeChatProvider(
            [
                "Hello[SYS",
                "TEM_PROMPT]private platform policy",
                "[/SYSTEM_",
                "PROMPT] there.",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Sanitized reply", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Say hello.", "memory_mode": "off"},
            ).json()
            completed = running.wait_job(accepted["job"]["id"])

            self.assertEqual(completed["result"]["text"], "Hello there.")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(detail["messages"][-1]["text"], "Hello there.")
            replay = list(
                running.services.conversations.broker.subscribe(
                    accepted["turn"]["id"],
                    {"status": "completed"},
                )
            )
            streamed = "".join(
                str(event.data.get("text") or "") for event in replay if event.event == "assistant.delta"
            )
            self.assertEqual(streamed, "Hello there.")
            self.assertNotIn("private platform policy", streamed)
            self.assertNotIn("SYSTEM_PROMPT", streamed)

    def test_legacy_prompt_envelope_is_hidden_and_excluded_from_future_context(self):
        provider = FakeChatProvider(["Safe next reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Legacy sanitized history", "memory_mode": "off"},
            ).json()
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                uow.repo.add_message(
                    chat["id"],
                    "assistant",
                    "[SYSTEM_PROMPT]private platform policy[/SYSTEM_PROMPT]",
                )

            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(
                detail["messages"][-1]["text"],
                "Sorry, something went wrong with that reply. Please try again.",
            )
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Continue normally.", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])
            model_context = json.dumps(provider.requests[0].messages)
            self.assertNotIn("private platform policy", model_context)
            self.assertNotIn("SYSTEM_PROMPT", model_context)

    def test_disconnecting_turn_event_subscription_does_not_cancel_generation(self):
        gate = threading.Event()
        provider = FakeChatProvider(["still completes"], gate=gate)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Disconnect"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "keep running"},
            ).json()
            self.assertTrue(provider.started.wait(2))
            turn_id = started["turn"]["id"]
            job_id = started["job"]["id"]
            snapshot = running.client.get(f"/api/v1/turns/{turn_id}").json()
            subscription = running.services.broker.subscribe(turn_id, snapshot)
            self.assertEqual(next(subscription).event, "turn.snapshot")
            subscription.close()
            self.assertEqual(running.client.get(f"/api/v1/jobs/{job_id}").json()["status"], "running")
            gate.set()
            self.assertEqual(running.wait_job(job_id)["status"], "completed")

    def test_turn_job_redacts_unexpected_provider_failures(self):
        class UnsafeProvider(FakeChatProvider):
            def stream(self, request, _cancellation):
                self.requests.append(request)
                raise RuntimeError("provider leaked sk-supersecret123456")
                yield  # pragma: no cover - keep this method a generator

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=UnsafeProvider()) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Failure"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "fail without leaking"},
            )
            self.assertEqual(accepted.status_code, 202, accepted.text)
            failed = running.wait_job(accepted.json()["job"]["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"], "The request failed unexpectedly.")
            self.assertNotIn("sk-supersecret123456", str(failed))
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual([message["role"] for message in detail["messages"]], ["user"])

    def test_cancelled_media_discards_an_uncancellable_provider_result(self):
        class LateMediaProvider:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()

            def generate(self, _request, _cancellation):
                self.started.set()
                self.release.wait(2)
                return MediaArtifact("image", b"late-image", ".png", "image/png")

        provider = LateMediaProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["openai-image"] = provider
            saved = running.client.put(
                "/api/v1/settings",
                json={
                    "openai_api_key": "sk-media-cancel-test",
                    "preferences": {"image_provider": "openai"},
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            started = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "late result"},
            )
            self.assertEqual(started.status_code, 202, started.text)
            job_id = started.json()["job_id"]
            self.assertTrue(provider.started.wait(2))
            self.assertEqual(running.client.delete(f"/api/v1/jobs/{job_id}").json()["status"], "cancelled")
            provider.release.set()
            self.assertTrue(running.services.jobs.queue.wait_until_idle(timeout=2))
            self.assertEqual(list(running.config.image_dir.iterdir()), [])
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                count = uow.session.scalar(select(func.count()).select_from(MediaFile))
            self.assertEqual(count, 0)

    def test_task_planner_automatically_starts_an_admitted_image(self):
        provider = FakeChatProvider(
            ["I can make that for you."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a small cat",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = SimpleNamespace(
                generate=lambda _request, _cancellation: MediaArtifact(
                    "image",
                    b"image-bytes",
                    ".png",
                    "image/png",
                )
            )
            saved = running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            chat = running.client.post("/api/v1/chats", json={"title": "Capability"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Generate an image of a small cat"},
            ).json()
            job = running.wait_job(started["job"]["id"])
            self.assertEqual(job["result"]["text"], "I’ll see what I can make for you.")
            requests = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["permission_mode"], "auto")
            self.assertEqual(
                requests[0]["arguments"],
                {
                    "prompt": "a small cat",
                    "operation": "generate",
                    "domains": [],
                    "content_tags": [],
                    "required_features": [],
                },
            )
            self.assertIsNotNone(requests[0]["job_id"])
            self.assertEqual(running.wait_job(requests[0]["job_id"])["status"], "completed")
            turn_id = started["turn"]["id"]
            turn = running.client.get(f"/api/v1/turns/{turn_id}").json()
            self.assertEqual(turn["accumulated_text"], "I’ll see what I can make for you.")
            events = list(running.services.broker.subscribe(turn_id, turn))
            deltas = "".join(
                event.data.get("text", "") for event in events if event is not None and event.event == "assistant.delta"
            )
            self.assertEqual(deltas, "I’ll see what I can make for you.")
            self.assertEqual(provider.requests[0].tools, [])
            planner_request = next(
                request for request in provider.task_requests if provider._task_role(request) == CAPABILITY_PLANNING
            )
            self.assertEqual(
                planner_request.response_format["properties"]["requests"]["items"]["properties"]["capability_key"][
                    "enum"
                ],
                ["media.generate_image"],
            )

    def test_explicit_text_only_turn_cannot_create_a_capability_request(self):
        provider = FakeChatProvider(
            ["managed reclamation passed"],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "managed reclamation passed",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = object()
            saved = running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            chat = running.client.post("/api/v1/chats", json={"title": "Text only"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Reply with exactly: managed reclamation passed"},
            ).json()
            job = running.wait_job(started["job"]["id"])

            self.assertEqual(job["result"]["text"], "managed reclamation passed")
            pending = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            self.assertEqual(pending, [])
            self.assertFalse(
                any(provider._task_role(request) == CAPABILITY_PLANNING for request in provider.task_requests)
            )

    def test_prompt_contains_current_user_message_once(self):
        provider = FakeChatProvider(["Done."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={"workspace_id": workspace["id"], "title": "New chat"},
            ).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "unique current input"},
            ).json()
            running.wait_job(started["job"]["id"])
            messages = provider.requests[0].messages
            self.assertEqual(
                [message for message in messages if message["content"] == "unique current input"],
                [{"role": "user", "content": "unique current input"}],
            )

    def test_running_turn_cancellation_is_idempotent_and_persists_no_assistant(self):
        gate = threading.Event()
        provider = FakeChatProvider(["late response"], gate=gate)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Cancel"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "cancel me"},
            ).json()
            job_id = started["job"]["id"]
            self.assertTrue(provider.started.wait(2))
            first = running.client.delete(f"/api/v1/jobs/{job_id}")
            second = running.client.delete(f"/api/v1/jobs/{job_id}")
            self.assertEqual(first.json()["status"], "cancelled")
            self.assertEqual(second.json()["status"], "cancelled")
            gate.set()
            self.assertEqual(running.wait_job(job_id)["status"], "cancelled")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual([message["role"] for message in detail["messages"]], ["user"])

    def test_provider_failure_is_safe_and_not_persisted_as_assistant_text(self):
        provider = FakeChatProvider(
            error=ProviderError(
                provider="ollama",
                code="unavailable",
                user_message="The model provider is unavailable.",
                retryable=True,
            )
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Failure"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "fail safely"},
            ).json()
            job = running.wait_job(started["job"]["id"])
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error"], "The model provider is unavailable.")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual([message["role"] for message in detail["messages"]], ["user"])
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["status"], "failed")
            self.assertEqual(turn["error"]["code"], "unavailable")

    def test_state_machine_rejects_illegal_transitions(self):
        job = SimpleNamespace(
            status="queued",
            progress="",
            updated_at=0,
            started_at=None,
            completed_at=None,
            error=None,
        )
        transition_job(job, "running", progress="Running")
        transition_job(job, "completed", progress="Completed")
        with self.assertRaises(InvalidJobTransition):
            transition_job(job, "running", progress="Running")
        turn = SimpleNamespace(
            status="queued",
            started_at=None,
            completed_at=None,
            error_code=None,
            error_message=None,
        )
        transition_turn(turn, "cancelled", code="cancelled", message="Request cancelled.")
        with self.assertRaises(InvalidJobTransition):
            transition_turn(turn, "completed")

    def test_every_job_and_turn_state_transition_matches_the_legal_matrix(self):
        states = tuple(LEGAL_TRANSITIONS)
        for source in states:
            for target in states:
                should_succeed = target == source or target in LEGAL_TRANSITIONS[source]
                job = SimpleNamespace(
                    status=source,
                    progress="",
                    updated_at=0,
                    started_at=None,
                    completed_at=None,
                    error=None,
                )
                turn = SimpleNamespace(
                    status=source,
                    started_at=None,
                    completed_at=None,
                    error_code=None,
                    error_message=None,
                )
                with self.subTest(kind="job", source=source, target=target):
                    if should_succeed:
                        transition_job(job, target, progress=target)
                        self.assertEqual(job.status, target)
                    else:
                        with self.assertRaises(InvalidJobTransition):
                            transition_job(job, target, progress=target)
                with self.subTest(kind="turn", source=source, target=target):
                    if should_succeed:
                        transition_turn(turn, target)
                        self.assertEqual(turn.status, target)
                    else:
                        with self.assertRaises(InvalidJobTransition):
                            transition_turn(turn, target)


if __name__ == "__main__":
    unittest.main()
