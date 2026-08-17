"""Local and cloud are both offered. Which one you have is never a surprise.

This product runs local and cloud providers side by side on purpose. Somebody
who wants a fully self-contained assistant should be able to have one; somebody
who wants better transcription than their hardware can manage should be able to
have that instead.

What is not acceptable is a cloud provider arriving by default, by fallback, or
by nobody having said so. These pin the three: defaults are local or off,
choosing cloud stays possible, and the product can say in one place where each
part of a conversation goes.
"""

from pathlib import Path
import tempfile
import unittest

from app.data_locality import (
    CLOUD,
    LOCAL,
    OFF,
    UNKNOWN,
    conversation_locality,
    is_cloud,
    leaves_this_machine,
    locality,
)
from app.task_contracts import TASK_ROLES
from tests.support import TestApp


class LocalityNamingTests(unittest.TestCase):
    def test_a_provider_is_named_local_cloud_or_off(self):
        self.assertEqual(locality("ollama"), LOCAL)
        self.assertEqual(locality("local-image"), LOCAL)
        self.assertEqual(locality("openai"), CLOUD)
        self.assertEqual(locality("openai-image"), CLOUD)
        self.assertEqual(locality("disabled"), OFF)
        self.assertEqual(locality(None), OFF)

    def test_an_unrecognised_provider_says_nobody_knows(self):
        # Calling it local would be a privacy claim made on no evidence, and
        # the person who most needs this answer is the one it would mislead.
        self.assertEqual(locality("some-new-service"), UNKNOWN)
        self.assertFalse(is_cloud("some-new-service"))
        # It is not cloud, but it still counts as leaving, because nobody can
        # say it does not.
        self.assertTrue(leaves_this_machine("some-new-service"))
        self.assertFalse(leaves_this_machine("ollama"))


class LocalitySummaryTests(unittest.TestCase):
    def test_every_part_of_a_conversation_is_accounted_for(self):
        summary = conversation_locality({"stt_provider": "disabled", "tts_provider": "local"}, "ollama", True)

        labels = [part["label"] for part in summary["parts"]]
        # Named as things that happen in a conversation, not as subsystems.
        self.assertIn("What you say", labels)
        self.assertIn("What you hear", labels)
        self.assertIn("Background jobs", labels)
        self.assertIn("Finding memories", labels)
        self.assertTrue(summary["everything_local"])

    def test_one_cloud_provider_is_enough_to_say_so(self):
        summary = conversation_locality({"stt_provider": "openai", "tts_provider": "local"}, "ollama", True)

        self.assertFalse(summary["everything_local"])
        speech = next(part for part in summary["parts"] if part["label"] == "What you say")
        self.assertEqual(speech["locality"], CLOUD)

    def test_one_unclassified_provider_is_enough_to_stop_the_claim(self):
        summary = conversation_locality({"stt_provider": "some-new-service"}, "ollama", True)

        # "Everything runs here" is a claim, and one unclassified part is
        # enough that nobody can make it honestly.
        self.assertFalse(summary["everything_local"])
        speech = next(part for part in summary["parts"] if part["label"] == "What you say")
        self.assertEqual(speech["locality"], UNKNOWN)

    def test_something_switched_off_is_not_counted_as_leaving(self):
        summary = conversation_locality({"stt_provider": "disabled", "tts_provider": "disabled"}, "ollama", False)

        # Off is not local, and it is certainly not cloud. Reporting it as
        # either would be a small lie that adds up.
        self.assertEqual(
            {part["locality"] for part in summary["parts"]},
            {LOCAL, OFF},
        )
        self.assertTrue(summary["everything_local"])


class LocalityRouteTests(unittest.TestCase):
    def test_a_fresh_account_is_entirely_local(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            reported = running.client.get("/api/v1/data-locality")

            self.assertEqual(reported.status_code, 200, reported.text)
            body = reported.json()
            # Nothing cloud arrives by default. That is the whole point.
            self.assertTrue(body["everything_local"], body)
            self.assertNotIn(CLOUD, {part["locality"] for part in body["parts"]})

    def test_choosing_a_cloud_provider_is_reported_rather_than_prevented(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"stt_provider": "openai", "openai_api_key": "sk-test1234567890"},
            )
            body = running.client.get("/api/v1/data-locality").json()

            self.assertFalse(body["everything_local"])
            speech = next(part for part in body["parts"] if part["label"] == "What you say")
            self.assertEqual(speech["locality"], CLOUD)


class CloudRemainsAvailableTests(unittest.TestCase):
    def _profile(self, **overrides) -> dict:
        values = {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
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

    def test_a_cloud_task_provider_can_still_be_chosen(self):
        from app.openai_task_provider import OpenAITaskModelProvider

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.task_providers["openai"] = OpenAITaskModelProvider()
            saved = running.client.put("/api/v1/task-models/title_generation", json=self._profile())

            # Someone else's deployment, someone else's judgement. Removing the
            # option would be deciding for them.
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["provider"], "openai")

    def test_no_role_starts_out_on_a_cloud_provider(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            profiles = running.client.get("/api/v1/task-models").json()["items"]

            self.assertEqual({profile["role"] for profile in profiles}, set(TASK_ROLES))
            for profile in profiles:
                self.assertFalse(is_cloud(profile["provider"]), profile)
                self.assertFalse(is_cloud(profile["fallback_provider"]), profile)


if __name__ == "__main__":
    unittest.main()
