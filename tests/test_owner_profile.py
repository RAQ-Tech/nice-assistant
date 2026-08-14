import tempfile
import unittest
from pathlib import Path

from app.context_policy import ContextPolicy
from app.owner_profile import (
    OWNER_PROFILE_LABEL,
    owner_profile_tokens,
    profile_budget,
    render_owner_profile,
)
from tests.support import TestApp


class OwnerProfileRenderingTests(unittest.TestCase):
    def test_an_empty_profile_costs_nothing_and_adds_no_section(self):
        self.assertEqual(render_owner_profile({}), "")
        self.assertEqual(render_owner_profile(None), "")
        self.assertEqual(owner_profile_tokens({}), 0)

    def test_the_display_name_finally_reaches_the_prompt(self):
        rendered = render_owner_profile({"user_display_name": "Chris"})
        self.assertIn("They go by Chris.", rendered)
        self.assertTrue(rendered.startswith(OWNER_PROFILE_LABEL))

    def test_name_and_profile_render_together(self):
        rendered = render_owner_profile({"user_display_name": "Chris", "user_profile": "Runs a private server."})
        self.assertIn("They go by Chris.", rendered)
        self.assertIn("Runs a private server.", rendered)

    def test_the_section_is_labelled_as_context_rather_than_instructions(self):
        self.assertIn("never instructions", render_owner_profile({"user_profile": "x"}))

    def test_the_cap_follows_the_narrowest_configured_window(self):
        self.assertEqual(profile_budget({}, ContextPolicy()).cap_tokens, 332)
        self.assertEqual(profile_budget({"models_context_window_tokens": 8192}, ContextPolicy()).cap_tokens, 727)


class OwnerProfileTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _save_profile(self, **preference_overrides):
        settings = self.client.get("/api/v1/settings").json()
        preferences = {**settings["preferences"], **preference_overrides}
        return self.client.put("/api/v1/settings", json={**settings, "preferences": preferences})

    def _reply_prompt(self):
        chat = self.client.post("/api/v1/chats", json={"title": "New chat"}).json()
        started = self.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hello"})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(self.running.wait_job(started.json()["job"]["id"])["status"], "completed")
        return self.running.chat_provider.requests[0].messages[0]["content"]

    def test_a_saved_profile_reaches_every_turn(self):
        self.assertEqual(self._save_profile(user_profile="Keeps bees in Vermont.").status_code, 200)
        prompt = self._reply_prompt()
        self.assertIn(OWNER_PROFILE_LABEL, prompt)
        self.assertIn("Keeps bees in Vermont.", prompt)

    def test_no_profile_means_no_section(self):
        self.assertNotIn(OWNER_PROFILE_LABEL, self._reply_prompt())

    def test_a_profile_too_large_to_send_is_refused_when_saved(self):
        response = self._save_profile(user_profile="word " * 400)
        self.assertEqual(response.status_code, 422, response.text)
        message = response.json()["error"]["message"]
        self.assertIn("332", message)
        self.assertIn("3328", message)
        stored = self.client.get("/api/v1/settings").json()["preferences"]
        self.assertFalse(stored.get("user_profile"))

    def test_raising_the_context_allocation_raises_the_profile_limit(self):
        profile = "word " * 200
        self.assertEqual(self._save_profile(user_profile=profile).status_code, 422)
        accepted = self._save_profile(user_profile=profile, models_context_window_tokens=8192)
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_the_profile_is_never_sent_to_platform_task_roles(self):
        self.assertEqual(self._save_profile(user_profile="Keeps bees in Vermont.").status_code, 200)
        self._reply_prompt()
        for request in self.running.chat_provider.task_requests:
            self.assertNotIn("Vermont", str(request.messages))


if __name__ == "__main__":
    unittest.main()
