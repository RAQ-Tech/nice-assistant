import json
import unittest
from unittest import mock

from app.openai_task_provider import STRUCTURED_OUTPUT_MODELS, OpenAITaskModelProvider
from app.provider_contracts import CancellationToken, ProviderError, ProviderStatus
from app.provider_registry import ProviderRegistry
from app.task_contracts import MEMORY_EXTRACTION, MemoryExtractionTaskInput, task_definition


def _request(schema=None, **options):
    from app.provider_contracts import ChatRequest

    return ChatRequest(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        options={"api_key": "sk-test", "num_predict": 128, "temperature": 0.0, **options},
        response_format=schema,
        timeout_seconds=30,
    )


class OpenAITaskProviderTests(unittest.TestCase):
    """A second adapter is the only thing that proves the task contract is not
    quietly shaped around Ollama."""

    def setUp(self):
        self.provider = OpenAITaskModelProvider()
        self.token = CancellationToken()

    def test_it_only_advertises_models_that_accept_a_json_schema(self):
        self.assertEqual(self.provider.list_models(), list(STRUCTURED_OUTPUT_MODELS))

    def test_health_claims_installation_and_nothing_else(self):
        health = self.provider.health()
        self.assertEqual(health.status, ProviderStatus.READY)
        # Neither reachability nor credentials: this object has no account, and
        # saying "Configured" without one is what made a keyless profile report
        # itself ready.
        self.assertIn("Adapter installed", health.message)
        self.assertNotIn("Configured", health.message)

    def test_it_declares_that_it_needs_an_account_key(self):
        # Declared on the adapter so readiness never has to special-case a
        # provider name.
        self.assertTrue(self.provider.requires_account_api_key)
        self.assertIn("API key", self.provider.missing_credential_message)

    def test_a_missing_account_key_fails_before_any_request(self):
        with mock.patch("app.openai_task_provider.openai_auth_json_request") as call:
            with self.assertRaises(ProviderError) as caught:
                self.provider.generate(_request(api_key=""), self.token)
        call.assert_not_called()
        self.assertEqual(caught.exception.code, "openai_api_key_missing")

    def test_the_role_schema_is_sent_as_a_strict_structured_output_envelope(self):
        definition = task_definition(MEMORY_EXTRACTION)
        schema = definition.response_schema(MemoryExtractionTaskInput(user_text="I keep bees."))
        captured = {}

        def fake(url, payload, api_key, timeout):
            captured.update(payload=payload, api_key=api_key, timeout=timeout)
            return {"choices": [{"message": {"content": '{"candidates":[]}'}}]}

        with mock.patch("app.openai_task_provider.openai_auth_json_request", fake):
            raw = self.provider.generate(_request(schema), self.token)

        self.assertEqual(raw, '{"candidates":[]}')
        response_format = captured["payload"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["schema"], schema)
        self.assertEqual(captured["api_key"], "sk-test")

    def test_its_output_satisfies_the_same_parser_ollama_output_does(self):
        definition = task_definition(MEMORY_EXTRACTION)
        task_input = MemoryExtractionTaskInput(user_text="I keep bees.")
        body = {
            "choices": [
                {"message": {"content": json.dumps({"candidates": [{"content": "Keeps bees.", "confidence": 0.9}]})}}
            ]
        }
        with mock.patch("app.openai_task_provider.openai_auth_json_request", return_value=body):
            raw = self.provider.generate(_request(definition.response_schema(task_input)), self.token)
        output = definition.parse_output(raw, task_input, 384)
        self.assertEqual(output.candidates[0].content, "Keeps bees.")
        self.assertEqual(output.candidates[0].confidence, 0.9)

    def test_a_refusal_is_a_terminal_outcome_not_a_malformed_response(self):
        body = {"choices": [{"message": {"refusal": "I cannot help with that.", "content": None}}]}
        with mock.patch("app.openai_task_provider.openai_auth_json_request", return_value=body):
            with self.assertRaises(ProviderError) as caught:
                self.provider.generate(_request({}), self.token)
        self.assertEqual(caught.exception.code, "task_refused")

    def test_an_unexpected_shape_is_reported_without_leaking_the_body(self):
        body = {"internal_trace": "internal-trace-must-not-surface"}
        with mock.patch("app.openai_task_provider.openai_auth_json_request", return_value=body):
            with self.assertRaises(ProviderError) as caught:
                self.provider.generate(_request({}), self.token)
        self.assertEqual(caught.exception.code, "invalid_task_response")
        self.assertNotIn("internal-trace", caught.exception.user_message)

    def test_conversation_is_refused_because_this_adapter_does_not_serve_it(self):
        with self.assertRaises(ProviderError) as caught:
            list(self.provider.stream(_request({}), self.token))
        self.assertEqual(caught.exception.code, "streaming_not_supported")


class TaskProviderRegistryTests(unittest.TestCase):
    def test_task_providers_default_to_the_chat_providers(self):
        registry = ProviderRegistry(chat_providers={"ollama": object()})
        self.assertEqual(set(registry.task_providers), {"ollama"})

    def test_a_task_only_provider_is_not_offered_for_conversation(self):
        registry = ProviderRegistry(
            chat_providers={"ollama": object()},
            task_providers={"ollama": object(), "openai": OpenAITaskModelProvider()},
        )
        self.assertIn("openai", registry.task_providers)
        self.assertNotIn("openai", registry.chat_providers)
        with self.assertRaises(LookupError):
            registry.chat("openai")
        self.assertIsNotNone(registry.task("openai"))


if __name__ == "__main__":
    unittest.main()
