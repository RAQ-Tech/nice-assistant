"""Conversation text stays on this machine.

Five background roles read what was said - titles, summaries, memory extraction,
picture planning, and scene proposals - and memory extraction reads all of it and
decides what is true about the person using this. The owner decided on
2026-08-17 that none of it goes to a cloud provider.

Before this, the only thing stopping it was that the settings screen did not
offer the choice. A thing that is merely absent from a screen is one HTTP request
away from happening, so the refusal is now in the service and says why.
"""

from pathlib import Path
import tempfile
import unittest

from app.task_contracts import OFF_MACHINE_TASK_PROVIDERS, TASK_ROLES
from tests.support import TestApp


def profile(**overrides) -> dict:
    values = {
        "enabled": True,
        "provider": "ollama",
        "model": "llama3",
        "fallback_provider": None,
        "fallback_model": None,
        "max_input_tokens": 2048,
        "max_output_tokens": 64,
        "timeout_seconds": 30.0,
        "temperature": 0.0,
        "fallback_policy": "skip",
    }
    values.update(overrides)
    return values


class LocalOnlyTaskModelTests(unittest.TestCase):
    def test_no_role_accepts_an_off_machine_provider(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            for role in TASK_ROLES:
                for name in OFF_MACHINE_TASK_PROVIDERS:
                    refused = running.client.put(f"/api/v1/task-models/{role}", json=profile(provider=name))

                    self.assertEqual(refused.status_code, 400, f"{role}: {refused.text}")
                    # Named, and with the reason. "Provider is not configured"
                    # reads as a setup mistake somebody should go and fix.
                    self.assertIn(name, refused.text)
                    self.assertIn("on this machine", refused.text)

    def test_the_fallback_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            refused = running.client.put(
                "/api/v1/task-models/memory_extraction",
                json=profile(fallback_provider="openai", fallback_model="gpt-4o-mini"),
            )

            # A fallback runs the same text through the same provider; refusing
            # only the primary would leave the boundary open on the path that
            # gets used when something goes wrong.
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("fallback provider", refused.text)

    def test_a_local_provider_still_saves(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            saved = running.client.put("/api/v1/task-models/title_generation", json=profile())

            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["provider"], "ollama")

    def test_nothing_off_machine_is_wired_up_at_all(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()

            # Belt and braces: the refusal explains the decision, and the
            # provider is not registered either, so there is no live path even
            # for code that skips the service.
            for name in OFF_MACHINE_TASK_PROVIDERS:
                self.assertNotIn(name, running.services.providers.task_providers)


if __name__ == "__main__":
    unittest.main()
