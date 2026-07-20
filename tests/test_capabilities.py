import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.capability_contracts import CAPABILITY_LEGAL_TRANSITIONS, CapabilityRegistry
from app.capability_service import InvalidCapabilityTransition, transition_capability
from app.provider_contracts import ChatToolCall, MediaArtifact, ProviderError
from app.repositories import UnitOfWork
from app.service_errors import RequestError
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


class FakeImageProvider:
    def __init__(self, *, gate: threading.Event | None = None):
        self.gate = gate
        self.started = threading.Event()
        self.requests = []

    def generate(self, request, cancellation):
        self.requests.append(request)
        self.started.set()
        if self.gate:
            self.gate.wait(2)
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"image-bytes", ".png", "image/png")


class FlakyImageProvider(FakeImageProvider):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 1

    def generate(self, request, cancellation):
        self.requests.append(request)
        self.started.set()
        cancellation.raise_if_cancelled()
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise ProviderError("fake-image", "provider_unavailable", "The image provider is unavailable.", True)
        return MediaArtifact("image", b"image-bytes", ".png", "image/png")


class FakeVideoProvider(FakeImageProvider):
    def generate(self, request, cancellation):
        self.requests.append(request)
        self.started.set()
        cancellation.raise_if_cancelled()
        return MediaArtifact("video", b"video-bytes", ".mp4", "video/mp4")


class CapabilityTests(unittest.TestCase):
    def test_transition_matrix_rejects_every_illegal_state_change(self):
        states = set(CAPABILITY_LEGAL_TRANSITIONS)
        states.update({"completed", "failed", "cancelled", "denied", "expired"})
        for source in states:
            for target in states:
                if target == source or target in CAPABILITY_LEGAL_TRANSITIONS.get(source, set()):
                    continue
                request = SimpleNamespace(
                    id="request",
                    status=source,
                    decided_at=None,
                    started_at=None,
                    completed_at=None,
                    error_code=None,
                    error_message=None,
                    result_json=None,
                )
                with self.subTest(source=source, target=target):
                    with self.assertRaises(InvalidCapabilityTransition):
                        transition_capability(SimpleNamespace(), request, target, "failed")

    def test_registry_accepts_semantic_media_intent_only(self):
        registry = CapabilityRegistry()
        definition, requirements = registry.from_tool_call(
            ChatToolCall("generate_image", {"prompt": "a moonlit garden"}, "call-1")
        )
        self.assertEqual(definition.key, "media.generate_image")
        self.assertEqual(
            requirements.as_arguments(),
            {
                "prompt": "a moonlit garden",
                "operation": "generate",
                "domains": [],
                "content_tags": [],
                "required_features": [],
            },
        )
        with self.assertRaisesRegex(RequestError, "unsupported fields"):
            registry.from_tool_call(ChatToolCall("generate_image", {"prompt": "garden", "model": "force-this-model"}))

    def test_image_approval_endpoint_rejects_every_lifecycle_state(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            request_ids = {}
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                for status in ("pending_confirmation", "queued", "running", "completed"):
                    row, _created = uow.repo.add_capability_request(
                        user_id=user_id,
                        chat_id=None,
                        turn_id=None,
                        capability_key="media.generate_image",
                        arguments={"prompt": f"{status} picture"},
                        status=status,
                        permission_mode="confirm" if status == "pending_confirmation" else "auto",
                        idempotency_key=f"image-approval-rejected:{status}",
                    )
                    request_ids[status] = row.id

            for status, request_id in request_ids.items():
                with self.subTest(status=status):
                    response = running.client.post(f"/api/v1/capability-requests/{request_id}/approval")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("without per-image approval", response.text)

    def test_unavailable_model_tool_call_fails_without_creating_a_request(self):
        provider = FakeChatProvider(
            ["I made it."],
            tool_calls=[ChatToolCall("generate_image", {"prompt": "a garden"}, "call-1")],
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Disabled", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show a garden", "memory_mode": "off"},
            ).json()
            failed = running.wait_job(accepted["job"]["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertIn("not permitted", failed["error"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertEqual(requests, [])
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual([message["role"] for message in detail["messages"]], ["user"])

    def test_task_planned_video_requires_approval_and_records_result_context(self):
        provider = FakeChatProvider(
            ["I can create that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_video",
                            "prompt": "a moonlit garden video",
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
        video_provider = FakeVideoProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            owner_id = running.create_and_login()
            running.services.providers.media_providers["openai-video"] = video_provider
            running.client.put(
                "/api/v1/settings",
                json={
                    "openai_api_key": "sk-video-approval-test",
                    "preferences": {
                        "video_provider": "openai",
                    },
                },
            )
            definitions = running.client.get("/api/v1/capabilities").json()["items"]
            self.assertFalse(next(item for item in definitions if item["key"] == "media.generate_image")["available"])
            video_definition = next(item for item in definitions if item["key"] == "media.generate_video")
            self.assertTrue(video_definition["available"])
            self.assertEqual(video_definition["permission_mode"], "confirm")
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Video"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "Video companion",
                    "allow_image_sends": False,
                },
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "title": "Tools",
                    "memory_mode": "off",
                },
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me a moonlit garden video", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])
            pending = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            self.assertEqual(pending["status"], "pending_confirmation")
            self.assertIsNone(pending["job_id"])
            self.assertEqual(video_provider.requests, [])

            approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(approved.status_code, 200, approved.text)
            job = running.wait_job(approved.json()["job_id"])
            self.assertEqual(job["status"], "completed")
            completed = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["result"]["mediaId"], job["result"]["mediaId"])
            self.assertEqual(len(video_provider.requests), 1)
            history = running.client.get(f"/api/v1/capability-requests/{pending['id']}/events").json()
            self.assertEqual(
                [event["action"] for event in history["events"]],
                ["requested", "approved", "queued", "started", "completed"],
            )
            media = running.client.get(f"/api/v1/media/{completed['result']['mediaId']}")
            self.assertEqual(media.content, b"video-bytes")

            provider.tool_calls = []
            provider.chunks = ["Thanks for confirming."]
            second = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Thank you", "memory_mode": "off"},
            ).json()
            running.wait_job(second["job"]["id"])
            context_messages = provider.requests[-1].messages
            tool_assistant = next(message for message in context_messages if message.get("tool_calls"))
            tool_result = next(message for message in context_messages if message.get("role") == "tool")
            self.assertEqual(tool_assistant["tool_calls"][0]["function"]["name"], "generate_video")
            self.assertEqual(
                tool_assistant["tool_calls"][0]["function"]["arguments"],
                {"prompt": "a moonlit garden video"},
            )
            self.assertIn('"status":"completed"', tool_result["content"])

            credentials = {"username": "other", "password": "pass1234"}
            running.client.post("/api/v1/users", json=credentials)
            running.client.post("/api/v1/session", json=credentials)
            self.assertEqual(
                running.client.get(f"/api/v1/capability-requests/{pending['id']}").status_code,
                404,
            )
            self.assertNotEqual(owner_id, "")

    def test_explicit_image_request_runs_automatically_as_a_reload_safe_attachment(self):
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "a moonlit garden",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": False,
        }
        provider = FakeChatProvider(
            ["I’ll make that for you."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post("/api/v1/chats", json={"memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me a moonlit garden", "memory_mode": "off"},
            ).json()
            chat_job = running.wait_job(accepted["job"]["id"])
            running.wait_job(chat_job["result"]["followup_job_id"])
            request = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            self.assertEqual(request["permission_mode"], "auto")
            image_definition = next(
                item
                for item in running.client.get("/api/v1/capabilities").json()["items"]
                if item["key"] == "media.generate_image"
            )
            self.assertEqual(image_definition["permission_mode"], "auto")
            self.assertIsNotNone(request["job_id"])
            running.wait_job(request["job_id"])
            completed = running.client.get(f"/api/v1/capability-requests/{request['id']}").json()
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["attachment"]["status"], "completed")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            attached = [attachment for message in detail["messages"] for attachment in message.get("attachments", [])]
            self.assertEqual(len(attached), 1)
            self.assertEqual(attached[0]["content_url"], f"/api/v1/media/{completed['result']['mediaId']}")
            self.assertEqual(len(image_provider.requests), 1)

            story = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "Tell me a story where someone says, ‘generate an image of a lighthouse.’",
                    "memory_mode": "off",
                },
            ).json()
            story_job = running.wait_job(story["job"]["id"])
            running.wait_job(story_job["result"]["followup_job_id"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertEqual(len(requests), 1)
            self.assertEqual(len(image_provider.requests), 1)

    def test_disabled_persona_blocks_only_task_planned_images(self):
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "a moonlit garden",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": False,
        }
        provider = FakeChatProvider(
            ["I can create that for you."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Private"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "Quiet",
                    "allow_image_sends": False,
                },
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "memory_mode": "off",
                },
            ).json()

            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me a moonlit garden", "memory_mode": "off"},
            ).json()
            completed = running.wait_job(accepted["job"]["id"])
            self.assertEqual(completed["result"]["text"], "Picture sending is turned off for this persona.")
            self.assertEqual(
                running.client.get(
                    "/api/v1/capability-requests",
                    params={"chat_id": chat["id"]},
                ).json()["items"],
                [],
            )
            self.assertEqual(image_provider.requests, [])
            capability_tasks = [
                request for request in provider.task_requests if provider._task_role(request) == CAPABILITY_PLANNING
            ]
            self.assertEqual(capability_tasks, [])

            direct = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "a moonlit garden", "chat_id": chat["id"]},
            )
            self.assertEqual(direct.status_code, 202, direct.text)
            self.assertEqual(running.wait_job(direct.json()["job_id"])["status"], "completed")
            self.assertEqual(len(image_provider.requests), 1)

    def test_delayed_image_plan_keeps_the_turns_originating_persona(self):
        planning_gate = threading.Event()
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "a candid portrait of the selected persona",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": True,
        }
        provider = FakeChatProvider(
            ["I’ll make that for you."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
            task_gates={CAPABILITY_PLANNING: planning_gate},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/comfyui"}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Persona race"}).json()
            origin = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Origin"},
            ).json()
            replacement = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "Replacement",
                    "allow_image_sends": False,
                },
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": origin["id"],
                    "memory_mode": "off",
                },
            ).json()

            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a candid portrait", "memory_mode": "off"},
            ).json()
            self.assertTrue(provider.task_started[CAPABILITY_PLANNING].wait(1))
            primary = running.client.get(f"/api/v1/jobs/{accepted['job']['id']}").json()
            self.assertEqual(primary["status"], "completed")
            switched = running.client.put(
                f"/api/v1/chats/{chat['id']}",
                json={"persona_id": replacement["id"]},
            )
            self.assertEqual(switched.status_code, 200, switched.text)

            planning_gate.set()
            running.wait_job(primary["result"]["followup_job_id"])
            request = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            conditioning = request["media_plan"]["identity_conditioning"]
            self.assertEqual(conditioning["persona_id"], origin["id"])
            self.assertNotEqual(conditioning["persona_id"], replacement["id"])

    def test_failed_chat_attachment_can_retry_against_current_policy(self):
        image_provider = FlakyImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post("/api/v1/chats", json={"memory_mode": "off"}).json()
            started = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "a garden", "chat_id": chat["id"]},
            ).json()
            failed_job = running.wait_job(started["job_id"])
            self.assertEqual(failed_job["status"], "failed")
            failed = running.client.get(f"/api/v1/capability-requests/{started['capability_request_id']}").json()
            self.assertTrue(failed["attachment"]["retry_available"])

            retried = running.client.post(f"/api/v1/capability-requests/{failed['id']}/retry")
            self.assertEqual(retried.status_code, 200, retried.text)
            replacement = retried.json()
            self.assertEqual(replacement["retry_of_request_id"], failed["id"])
            self.assertEqual(replacement["permission_mode"], "auto")
            running.wait_job(replacement["job_id"])
            replacement = running.client.get(f"/api/v1/capability-requests/{replacement['id']}").json()
            self.assertEqual(replacement["attachment"]["status"], "completed")
            history = running.client.get(f"/api/v1/capability-requests/{failed['id']}/events").json()
            self.assertEqual(history["events"][-1]["action"], "retried")

    def test_media_readiness_keeps_basic_generation_separate_from_optional_identity(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            running.services.provider_service.check = lambda _user_id, provider, _overrides=None: {
                "ok": True,
                "provider": provider,
                "status": "ready",
                "message": "Automatic1111 is reachable.",
            }

            response = running.client.get("/api/v1/media/readiness")
            self.assertEqual(response.status_code, 200, response.text)
            readiness = response.json()
            self.assertEqual(readiness["provider"]["key"], "automatic1111")
            self.assertTrue(readiness["provider"]["reachable"])
            self.assertTrue(readiness["basic_generation"]["ready"])
            self.assertFalse(readiness["optional_identity"]["ready"])
            self.assertIn("Basic images", readiness["optional_identity"]["message"])

    def test_persona_subject_flag_prevents_unrelated_images_from_inheriting_identity_control(self):
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "an empty greenhouse at sunrise",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": ["identity_control"],
            "persona_subject": False,
        }
        provider = FakeChatProvider(
            ["Maybe we could visit it together."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/comfyui"}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Greenhouse"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Companion"},
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Images", "memory_mode": "off", "persona_id": persona["id"]},
            ).json()

            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "Show an empty greenhouse; do not include the persona",
                    "memory_mode": "off",
                },
            ).json()
            running.wait_job(turn["job"]["id"])
            general = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            self.assertEqual(general["media_plan"]["status"], "ready")
            self.assertEqual(general["media_plan"]["requirements"]["required_features"], [])

            planned["prompt"] = "a selfie of the selected persona"
            planned["required_features"] = []
            planned["persona_subject"] = True
            second = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a selfie", "memory_mode": "off"},
            ).json()
            running.wait_job(second["job"]["id"])
            requests = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            persona_request = next(
                item for item in requests if item["arguments"]["prompt"] == "a selfie of the selected persona"
            )
            self.assertEqual(persona_request["media_plan"]["status"], "ready")
            self.assertEqual(
                persona_request["media_plan"]["requirements"]["required_features"],
                ["identity_control"],
            )
            self.assertEqual(persona_request["media_plan"]["identity_conditioning"]["status"], "unconditioned")

    def test_task_planned_capability_turn_still_extracts_memory_candidates(self):
        provider = FakeChatProvider(
            ["I can create that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a moonlit garden",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
            memory_candidates=[
                {
                    "content": "The user prefers moonlit gardens.",
                    "scope": "chat",
                    "confidence": 0.8,
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Memory with tools", "memory_mode": "saved"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "I prefer moonlit gardens. Show me one.",
                    "memory_mode": "saved",
                },
            ).json()

            completed = running.wait_job(accepted["job"]["id"])
            extraction = running.wait_job(completed["result"]["memory_extraction_job_id"])

            self.assertEqual(extraction["status"], "completed")
            self.assertEqual(extraction["result"]["candidate_count"], 1)
            requests = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            self.assertEqual(requests[0]["permission_mode"], "auto")
            self.assertIsNotNone(requests[0]["job_id"])
            running.wait_job(requests[0]["job_id"])
            request = running.client.get(f"/api/v1/capability-requests/{requests[0]['id']}").json()
            self.assertEqual(request["status"], "completed")
            memories = running.client.get("/api/v1/memories", params={"status": "pending"}).json()["items"]
            self.assertEqual(memories[0]["content"], "The user prefers moonlit gardens.")

    def test_explicit_image_idempotency_is_safe_to_repeat(self):
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )

            headers = {"Idempotency-Key": "explicit-image-1"}
            payload = {"prompt": "a green valley", "provider": "local/automatic1111"}
            explicit_first = running.client.post("/api/v1/media/image-jobs", json=payload, headers=headers)
            explicit_second = running.client.post("/api/v1/media/image-jobs", json=payload, headers=headers)
            self.assertEqual(explicit_first.status_code, 202, explicit_first.text)
            self.assertEqual(explicit_second.status_code, 202, explicit_second.text)
            self.assertEqual(explicit_first.json()["job_id"], explicit_second.json()["job_id"])
            self.assertEqual(
                explicit_first.json()["capability_request_id"],
                explicit_second.json()["capability_request_id"],
            )
            mismatch = running.client.post(
                "/api/v1/media/image-jobs",
                json={**payload, "prompt": "a different valley"},
                headers=headers,
            )
            self.assertEqual(mismatch.status_code, 409)
            running.wait_job(explicit_first.json()["job_id"])
            self.assertEqual(len(image_provider.requests), 1)

    def test_cancelling_a_running_capability_discards_the_artifact(self):
        release = threading.Event()
        image_provider = FakeImageProvider(gate=release)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            started = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "cancel this", "provider": "local/automatic1111"},
            ).json()
            self.assertTrue(image_provider.started.wait(2))
            cancelled = running.client.delete(f"/api/v1/capability-requests/{started['capability_request_id']}").json()
            self.assertEqual(cancelled["status"], "cancelled")
            release.set()
            self.assertTrue(running.services.jobs.queue.wait_until_idle(timeout=2))
            current = running.client.get(f"/api/v1/capability-requests/{started['capability_request_id']}").json()
            self.assertEqual(current["status"], "cancelled")
            self.assertEqual(list(running.config.image_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
