"""A task model with no credential says so, instead of reporting itself ready.

An OpenAI profile could be saved with no account API key and still return
`ready: true`, because readiness asked the adapter whether it existed and never
asked the account whether it had a key. The run then failed with
`openai_api_key_missing`. Adapter installed, credentials configured, and live
verified are three different facts; this covers reporting them as three.

Saving a profile like this over the API is refused now - conversation text stays
on this machine, decided 2026-08-17, and `tests/test_local_only_task_models.py`
covers that refusal. These tests write the profile straight to the repository to
reach the readiness logic behind it. That logic is kept deliberately: the adapter
still exists so allowing a credentialled provider later is a line of wiring, and
this is what stops that line arriving with the original bug still in it.
"""

from pathlib import Path
import tempfile
import unittest

from app.openai_task_provider import OpenAITaskModelProvider
from app.task_contracts import TITLE_GENERATION
from tests.support import TestApp


ROLE = TITLE_GENERATION


def profile(**overrides) -> dict:
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


class TaskModelCredentialTests(unittest.TestCase):
    def _ready(self, running) -> None:
        """An account with the OpenAI adapter installed and no key configured."""

        self.user_id = running.create_and_login()
        running.services.providers.task_providers["openai"] = OpenAITaskModelProvider()

    def _save(self, running, **overrides):
        """Write the profile past the API, which refuses this provider by decision."""

        values = profile(**overrides)
        with running.services.task_models._uow() as uow:
            uow.repo.save_task_model_profile(self.user_id, ROLE, values)
        return values

    def _check(self, running) -> dict:
        response = running.client.post(f"/api/v1/task-models/{ROLE}/check")
        assert response.status_code == 200, response.text
        return response.json()

    def _set_key(self, running, value) -> None:
        response = running.client.put("/api/v1/settings", json={"openai_api_key": value})
        assert response.status_code == 200, response.text

    # -- the reported failure ---------------------------------------------

    def test_a_keyless_profile_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)

            readiness = self._check(running)

            self.assertFalse(readiness["ready"])
            self.assertEqual(readiness["status"], "unavailable")
            self.assertIn("API key", readiness["message"])

    def test_the_reason_is_the_missing_key_and_not_a_missing_adapter(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)

            readiness = self._check(running)

            # The adapter really is installed. Saying otherwise would send
            # somebody looking for the wrong problem.
            self.assertTrue(readiness["adapter_installed"])
            self.assertFalse(readiness["credentials_configured"])

    def test_readiness_never_claims_a_live_request_was_made(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)
            self._set_key(running, "not-a-real-key-for-unit-tests")

            readiness = self._check(running)

            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["credentials_configured"])
            # Nothing has been sent anywhere. Readiness is a claim about
            # configuration, and it says so.
            self.assertFalse(readiness["live_verified"])

    def test_a_blank_key_counts_as_no_key(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)
            self._set_key(running, "   ")

            readiness = self._check(running)

            self.assertFalse(readiness["ready"])
            self.assertFalse(readiness["credentials_configured"])

    def test_the_message_never_contains_the_key(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)
            self._set_key(running, "sk-not-a-real-key-for-unit-tests")

            self.assertNotIn("sk-not-a-real-key-for-unit-tests", str(self._check(running)))

    # -- fallback semantics ------------------------------------------------

    def test_a_keyless_primary_reports_fallback_ready_only_when_it_really_is(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            model = running.services.providers.task("ollama").list_models()[0]
            self._save(running, fallback_provider="ollama", fallback_model=model)

            readiness = self._check(running)

            self.assertTrue(readiness["ready"])
            self.assertEqual(readiness["status"], "fallback_ready")
            self.assertFalse(readiness["primary_ready"])
            self.assertTrue(readiness["fallback_ready"])
            self.assertEqual(readiness["fallback_effective_model"], model)

    def test_a_keyless_primary_with_a_keyless_fallback_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running, fallback_provider="openai", fallback_model="gpt-4o-mini")

            readiness = self._check(running)

            self.assertFalse(readiness["ready"])
            self.assertFalse(readiness["fallback_ready"])
            self.assertEqual(readiness["status"], "unavailable")

    # -- one reason at a time, in the order that helps ---------------------

    def test_a_missing_key_is_reported_before_an_unknown_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running, model="a-model-that-does-not-exist")

            # Both are wrong. The key is the one that has to be fixed first,
            # because until it is, the model list cannot even be trusted.
            self.assertIn("API key", self._check(running)["message"])

    def test_a_configured_key_with_an_unknown_model_reports_the_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running, model="a-model-that-does-not-exist")
            self._set_key(running, "not-a-real-key-for-unit-tests")

            readiness = self._check(running)

            self.assertFalse(readiness["ready"])
            self.assertTrue(readiness["credentials_configured"])
            self.assertIn("not installed", readiness["message"])

    # -- what readiness predicts is what the run does ----------------------

    def test_a_keyless_run_falls_back_rather_than_pretending(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            model = running.services.providers.task("ollama").list_models()[0]
            self._save(running, fallback_provider="ollama", fallback_model=model)

            chat = running.client.post("/api/v1/chats", json={"title": "New chat", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "hello there", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])

            runs = running.client.get("/api/v1/task-model-runs").json()["items"]
            titles = [run for run in runs if run["role"] == ROLE]
            self.assertTrue(titles, runs)
            # Readiness said fallback_ready; the run used the fallback. The two
            # answers agree, which is the whole point of the change.
            self.assertEqual(titles[0]["executed_provider"], "ollama")

    def test_a_keyless_run_with_no_fallback_records_the_credential_reason(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running)

            chat = running.client.post("/api/v1/chats", json={"title": "New chat", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "hello there", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])

            runs = running.client.get("/api/v1/task-model-runs").json()["items"]
            titles = [run for run in runs if run["role"] == ROLE]
            self.assertTrue(titles, runs)
            failed = titles[0]
            self.assertNotEqual(failed["status"], "completed")
            # The recorded reason is the missing credential, not a vague
            # provider failure, and it carries no key in it.
            self.assertIn("api_key", str(failed.get("error") or {}).lower())

    # -- a provider that needs nothing is unaffected -----------------------

    def test_a_local_provider_needs_no_credential(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            readiness = self._check(running)

            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["adapter_installed"])
            self.assertTrue(readiness["credentials_configured"])


if __name__ == "__main__":
    unittest.main()
