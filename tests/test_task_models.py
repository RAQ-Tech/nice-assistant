import json
import tempfile
import unittest
from pathlib import Path

from app.provider_contracts import CancellationToken, ProviderError
from app.task_contracts import (
    CAPABILITY_PLANNING,
    MEMORY_EXTRACTION,
    TITLE_GENERATION,
    AvailableCapability,
    CapabilityPlanningTaskInput,
    TaskContractError,
    explicitly_excludes_persona,
    is_explicit_text_only_request,
    task_definition,
)
from tests.support import FakeChatProvider, TestApp


class MultiModelProvider(FakeChatProvider):
    def __init__(self, *args, failing_models=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.failing_models = set(failing_models or [])

    def list_models(self):
        return ["persona-model", "task-model", "fallback-model"]

    def generate(self, request, cancellation):
        if self._task_role(request) and request.model in self.failing_models:
            raise ProviderError(
                provider="ollama",
                code="unavailable",
                user_message="The configured task model is unavailable.",
                retryable=True,
            )
        return super().generate(request, cancellation)


def updated_profile(client, role, **values):
    profiles = client.get("/api/v1/task-models").json()["items"]
    profile = next(item for item in profiles if item["role"] == role)
    body = {
        key: profile[key]
        for key in (
            "enabled",
            "provider",
            "model",
            "fallback_provider",
            "fallback_model",
            "max_input_tokens",
            "max_output_tokens",
            "timeout_seconds",
            "temperature",
            "fallback_policy",
        )
    }
    body.update(values)
    return client.put(f"/api/v1/task-models/{role}", json=body)


class TaskModelTests(unittest.TestCase):
    def test_explicit_text_only_guard_is_narrow_and_prefix_scoped(self):
        guarded = (
            "Reply with exactly: managed reclamation passed",
            "Please respond exactly speech outage chat survived",
            "Answer with only yes",
            "say exactly: ready",
            "Repeat only these words",
            "Return only with OK",
        )
        for user_text in guarded:
            with self.subTest(user_text=user_text):
                self.assertTrue(is_explicit_text_only_request(user_text))

        unguarded = (
            "Show me your outfit.",
            "Create an image. Reply with exactly: done",
            "Explain what it means to reply with exactly one word.",
            "Can you say exactly where the image was stored?",
        )
        for user_text in unguarded:
            with self.subTest(user_text=user_text):
                self.assertFalse(is_explicit_text_only_request(user_text))

    def test_profiles_are_owner_scoped_and_readiness_uses_installed_model(self):
        provider = MultiModelProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            profiles = running.client.get("/api/v1/task-models")
            self.assertEqual(profiles.status_code, 200)
            self.assertEqual(
                {item["role"] for item in profiles.json()["items"]},
                {"title_generation", "conversation_summary", "memory_extraction", "capability_planning"},
            )
            saved = updated_profile(
                running.client,
                TITLE_GENERATION,
                model="task-model",
                fallback_provider="ollama",
                fallback_model="fallback-model",
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            readiness = running.client.post(f"/api/v1/task-models/{TITLE_GENERATION}/check").json()
            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["primary_ready"])
            self.assertTrue(readiness["fallback_ready"])
            self.assertEqual(readiness["effective_model"], "task-model")

            running.create_and_login("other")
            other = running.client.get("/api/v1/task-models").json()["items"]
            other_title = next(item for item in other if item["role"] == TITLE_GENERATION)
            self.assertIsNone(other_title["model"])
            self.assertEqual(running.client.get("/api/v1/task-model-runs").json()["items"], [])

    def test_persona_and_title_roles_use_separate_models_and_a_content_free_audit(self):
        provider = MultiModelProvider(task_outputs={TITLE_GENERATION: {"title": "A Better Title"}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            saved = updated_profile(running.client, TITLE_GENERATION, model="task-model")
            self.assertEqual(saved.status_code, 200, saved.text)
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "New conversation", "model": "persona-model", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Help me plan a garden", "model": "persona-model", "memory_mode": "off"},
            ).json()
            completed = running.wait_job(accepted["job"]["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(provider.requests[-1].model, "persona-model")
            title_request = next(
                request for request in provider.task_requests if provider._task_role(request) == TITLE_GENERATION
            )
            self.assertEqual(title_request.model, "task-model")
            self.assertEqual(
                running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"], "A Better Title"
            )

            runs = running.client.get("/api/v1/task-model-runs", params={"role": TITLE_GENERATION}).json()["items"]
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "completed")
            self.assertEqual(runs[0]["executed_model"], "task-model")
            serialized = str(runs[0]).lower()
            self.assertNotIn("help me plan", serialized)
            self.assertNotIn("a better title", serialized)
            self.assertNotIn("prompt", runs[0])
            self.assertNotIn("output", runs[0])

    def test_placeholder_title_output_uses_deterministic_title_instead(self):
        provider = MultiModelProvider(task_outputs={TITLE_GENERATION: {"title": "New conversation."}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            updated_profile(running.client, TITLE_GENERATION, model="task-model")
            chat = running.client.post(
                "/api/v1/chats",
                json={"title": "New conversation", "model": "persona-model", "memory_mode": "off"},
            ).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Help me plan a glass greenhouse", "model": "persona-model", "memory_mode": "off"},
            ).json()
            self.assertEqual(running.wait_job(accepted["job"]["id"])["status"], "completed")
            title = running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"]
            self.assertEqual(title, "Help me plan a glass greenhouse")
            run = running.client.get("/api/v1/task-model-runs", params={"role": TITLE_GENERATION}).json()["items"][0]
            self.assertEqual(run["status"], "fallback")
            self.assertEqual(run["error"]["code"], "invalid_task_output")

    def test_primary_failure_uses_configured_fallback_model(self):
        provider = MultiModelProvider(
            failing_models={"task-model"},
            task_outputs={TITLE_GENERATION: {"title": "Fallback Title"}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            updated_profile(
                running.client,
                TITLE_GENERATION,
                model="task-model",
                fallback_provider="ollama",
                fallback_model="fallback-model",
            )
            result = running.services.task_models.run(
                user_id,
                TITLE_GENERATION,
                task_definition(TITLE_GENERATION).input_type("A first message"),
                CancellationToken(),
            )
            self.assertEqual(result.output.title, "Fallback Title")
            self.assertEqual(result.model, "fallback-model")
            self.assertTrue(result.fallback_used)
            run = running.client.get("/api/v1/task-model-runs").json()["items"][0]
            self.assertEqual(run["status"], "completed")
            self.assertTrue(run["fallback_used"])
            self.assertEqual([attempt["status"] for attempt in run["attempts"]], ["failed", "completed"])

    def test_failed_memory_role_redacts_errors_and_does_not_claim_fallback_use(self):
        provider = MultiModelProvider(
            task_errors={
                MEMORY_EXTRACTION: ProviderError(
                    provider="ollama",
                    code="auth_failed",
                    user_message="Authorization: Bearer sk-private-secret",
                )
            }
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            with self.assertRaises(ProviderError):
                running.services.task_models.run(
                    user_id,
                    MEMORY_EXTRACTION,
                    task_definition(MEMORY_EXTRACTION).input_type("I live in Maine."),
                    CancellationToken(),
                )
            run = running.client.get("/api/v1/task-model-runs").json()["items"][0]
            self.assertEqual(run["status"], "failed")
            self.assertFalse(run["fallback_used"])
            self.assertNotIn("sk-private-secret", str(run))

    def test_capability_contract_excludes_media_resource_selection(self):
        task_input = CapabilityPlanningTaskInput(
            user_text="Show me a portrait.",
            available_capabilities=(AvailableCapability("media.generate_image", "Generate image", "Create an image."),),
            persona_selected=True,
            available_features=("text_to_image", "identity_control"),
        )
        definition = task_definition(CAPABILITY_PLANNING)
        self.assertIn("identity_control", definition.messages(task_input)[0]["content"])
        payload = json.loads(definition.messages(task_input)[1]["content"])
        self.assertTrue(payload["persona_selected"])
        self.assertNotIn("assistant_text", payload)
        item_properties = definition.response_schema(task_input)["properties"]["requests"]["items"]["properties"]
        self.assertEqual(
            set(item_properties),
            {
                "capability_key",
                "prompt",
                "operation",
                "domains",
                "content_tags",
                "required_features",
                "persona_subject",
            },
        )
        self.assertEqual(item_properties["prompt"]["maxLength"], 1000)
        for field in ("domains", "content_tags"):
            self.assertNotIn("maxItems", item_properties[field])
            self.assertNotIn("enum", item_properties[field]["items"])
        self.assertEqual(
            item_properties["required_features"]["items"]["enum"],
            ["text_to_image", "identity_control"],
        )
        with self.assertRaises(TaskContractError):
            definition.parse_output(
                '{"requests":[{"capability_key":"media.generate_image","prompt":"portrait","operation":"generate","domains":[],"content_tags":[],"required_features":[],"persona_subject":true,"model":"forced"}]}',
                task_input,
                384,
            )

        unrelated = definition.parse_output(
            '{"requests":[{"capability_key":"media.generate_image","prompt":"an empty greenhouse","operation":"generate","domains":[],"content_tags":[],"required_features":["identity_control","text_to_image"],"persona_subject":false}]}',
            task_input,
            384,
        ).requests[0]
        self.assertFalse(unrelated.persona_subject)
        self.assertEqual(unrelated.required_features, ("text_to_image",))

        persona_image = definition.parse_output(
            '{"requests":[{"capability_key":"media.generate_image","prompt":"a selfie of the selected persona","operation":"generate","domains":[],"content_tags":[],"required_features":[],"persona_subject":true}]}',
            task_input,
            384,
        ).requests[0]
        self.assertTrue(persona_image.persona_subject)
        self.assertEqual(persona_image.required_features, ("identity_control",))

        explicitly_unrelated_input = CapabilityPlanningTaskInput(
            user_text=(
                "Could you make an image of a cozy glass greenhouse at sunrise? It doesn't need to include you."
            ),
            available_capabilities=(AvailableCapability("media.generate_image", "Generate image", "Create an image."),),
            persona_selected=True,
            available_features=("text_to_image", "identity_control"),
        )
        wrongly_personalized = definition.parse_output(
            '{"requests":[{"capability_key":"media.generate_image","prompt":"a cozy glass greenhouse at sunrise","operation":"generate","domains":[],"content_tags":[],"required_features":["identity_control","text_to_image"],"persona_subject":true}]}',
            explicitly_unrelated_input,
            384,
        ).requests[0]
        self.assertFalse(wrongly_personalized.persona_subject)
        self.assertEqual(wrongly_personalized.required_features, ("text_to_image",))

    def test_explicit_persona_exclusion_guard_is_narrow(self):
        self.assertTrue(explicitly_excludes_persona("It doesn't need to include you."))
        self.assertTrue(explicitly_excludes_persona("Make it without the selected persona."))
        self.assertFalse(explicitly_excludes_persona("Make a selfie of you in a greenhouse."))
        self.assertFalse(explicitly_excludes_persona("Not just you: include your friend too."))
        self.assertFalse(explicitly_excludes_persona("A candid portrait of you without you knowing."))
        self.assertFalse(explicitly_excludes_persona("A portrait of you without people in the background."))

    def test_profile_validation_prevents_unsupported_deterministic_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            response = updated_profile(
                running.client,
                MEMORY_EXTRACTION,
                fallback_policy="deterministic",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("only for chat titles", response.text)

    def test_output_budget_is_enforced_after_structured_parsing(self):
        provider = MultiModelProvider(task_outputs={"conversation_summary": {"summary": "word " * 30}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            response = updated_profile(
                running.client,
                "conversation_summary",
                max_output_tokens=16,
                fallback_policy="skip",
            )
            self.assertEqual(response.status_code, 200, response.text)
            result = running.services.task_models.run(
                user_id,
                "conversation_summary",
                task_definition("conversation_summary").input_type("", "short transcript"),
                CancellationToken(),
            )
            self.assertEqual(result.output.summary, "")
            run = running.client.get("/api/v1/task-model-runs").json()["items"][0]
            self.assertEqual(run["status"], "fallback")
            self.assertEqual(run["error"]["code"], "task_output_budget_exceeded")

    def test_pre_cancelled_task_records_a_terminal_run(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            token = CancellationToken()
            token.cancel()
            with self.assertRaises(ProviderError):
                running.services.task_models.run(
                    user_id,
                    TITLE_GENERATION,
                    task_definition(TITLE_GENERATION).input_type("Hello"),
                    token,
                )
            run = running.client.get("/api/v1/task-model-runs").json()["items"][0]
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error"]["code"], "cancelled")


if __name__ == "__main__":
    unittest.main()
