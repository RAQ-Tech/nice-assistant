import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.capability_contracts import CAPABILITY_LEGAL_TRANSITIONS, CapabilityRegistry
from app.capability_service import InvalidCapabilityTransition, transition_capability
from app.provider_contracts import ChatToolCall, MediaArtifact
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

    def test_task_planned_request_requires_approval_and_records_result_context(self):
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
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            owner_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            definitions = running.client.get("/api/v1/capabilities").json()["items"]
            self.assertTrue(next(item for item in definitions if item["key"] == "media.generate_image")["available"])
            self.assertFalse(next(item for item in definitions if item["key"] == "media.generate_video")["available"])
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "Tools", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me a moonlit garden", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])
            pending = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            self.assertEqual(pending["status"], "pending_confirmation")
            self.assertIsNone(pending["job_id"])
            self.assertEqual(image_provider.requests, [])

            approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(approved.status_code, 200, approved.text)
            job = running.wait_job(approved.json()["job_id"])
            self.assertEqual(job["status"], "completed")
            completed = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["result"]["mediaId"], job["result"]["mediaId"])
            self.assertEqual(len(image_provider.requests), 1)
            history = running.client.get(f"/api/v1/capability-requests/{pending['id']}/events").json()
            self.assertEqual(
                [event["action"] for event in history["events"]],
                ["requested", "approved", "queued", "started", "completed"],
            )
            media = running.client.get(f"/api/v1/media/{completed['result']['mediaId']}")
            self.assertEqual(media.content, b"image-bytes")

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
            self.assertEqual(tool_assistant["tool_calls"][0]["function"]["name"], "generate_image")
            self.assertEqual(
                tool_assistant["tool_calls"][0]["function"]["arguments"],
                {"prompt": "a moonlit garden"},
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
                json={"text": "Show an empty greenhouse; do not include the persona", "memory_mode": "off"},
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
            self.assertEqual(persona_request["media_plan"]["status"], "blocked")
            self.assertEqual(
                persona_request["media_plan"]["requirements"]["required_features"],
                ["identity_control"],
            )
            self.assertIn("Open Settings", persona_request["media_plan"]["block"]["message"])

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
                json={"text": "I prefer moonlit gardens. Show me one.", "memory_mode": "saved"},
            ).json()

            completed = running.wait_job(accepted["job"]["id"])
            extraction = running.wait_job(completed["result"]["memory_extraction_job_id"])

            self.assertEqual(extraction["status"], "completed")
            self.assertEqual(extraction["result"]["candidate_count"], 1)
            requests = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"]
            self.assertEqual(requests[0]["status"], "pending_confirmation")
            memories = running.client.get("/api/v1/memories", params={"status": "pending"}).json()["items"]
            self.assertEqual(memories[0]["content"], "The user prefers moonlit gardens.")

    def test_denial_and_explicit_idempotency_are_safe_to_repeat(self):
        provider = FakeChatProvider(
            ["I can create that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a lighthouse",
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
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post("/api/v1/chats", json={"title": "Deny", "memory_mode": "off"}).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show a lighthouse", "memory_mode": "off"},
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            first = running.client.post(f"/api/v1/capability-requests/{pending['id']}/denial")
            second = running.client.post(f"/api/v1/capability-requests/{pending['id']}/denial")
            self.assertEqual(first.json()["status"], "denied")
            self.assertEqual(second.json()["status"], "denied")
            self.assertEqual(
                running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval").status_code,
                409,
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
