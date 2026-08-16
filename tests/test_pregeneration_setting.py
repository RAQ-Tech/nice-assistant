"""Pre-generation as something an owner sets, not something a deployment fixes.

It used to be read from environment variables at startup, so a control in the
browser would have moved and changed nothing. The runner now reads the stored
value on every pass. The deployment keeps one veto, because this runs the GPU
unattended.
"""

from pathlib import Path
import tempfile
import unittest

from app.pregeneration import PregenerationPolicy, policy_for_owner, validate_preferences
from app.service_errors import RequestError
from app.provider_contracts import MediaArtifact
from tests.support import TestApp


class _Provider:
    name = "local-image"

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


DEPLOYMENT = PregenerationPolicy(enabled=True, start_hour=2, end_hour=6, max_per_run=3)
FORBIDDEN = PregenerationPolicy(enabled=False, start_hour=2, end_hour=6, max_per_run=3)


class OwnerPolicyTests(unittest.TestCase):
    def test_an_owner_with_no_setting_gets_the_deployment_values(self):
        policy = policy_for_owner({}, DEPLOYMENT)

        self.assertEqual((policy.enabled, policy.start_hour, policy.end_hour), (True, 2, 6))

    def test_an_owner_setting_replaces_the_window(self):
        policy = policy_for_owner({"pregeneration_start_hour": 23, "pregeneration_end_hour": 5}, DEPLOYMENT)

        self.assertEqual((policy.start_hour, policy.end_hour), (23, 5))

    def test_an_owner_can_switch_it_off(self):
        self.assertFalse(policy_for_owner({"pregeneration_enabled": False}, DEPLOYMENT).enabled)

    def test_an_owner_cannot_switch_on_what_the_deployment_forbids(self):
        # This runs the GPU unattended overnight, and the machine has overheated
        # before. The deployment keeps the last word.
        self.assertFalse(policy_for_owner({"pregeneration_enabled": True}, FORBIDDEN).enabled)

    def test_a_nonsense_value_falls_back_rather_than_breaking_the_runner(self):
        policy = policy_for_owner({"pregeneration_start_hour": "midnight", "pregeneration_max_per_run": 0}, DEPLOYMENT)

        self.assertEqual((policy.start_hour, policy.max_per_run), (2, 3))


class ValidationTests(unittest.TestCase):
    def test_a_window_that_never_matches_is_refused(self):
        with self.assertRaises(RequestError) as caught:
            validate_preferences({"pregeneration_start_hour": 4, "pregeneration_end_hour": 4})

        # A switch that is on, a schedule that looks set, and nothing ever
        # happening is the worst of the three outcomes.
        self.assertIn("never matches", str(caught.exception))

    def test_an_hour_outside_the_clock_is_refused(self):
        with self.assertRaises(RequestError):
            validate_preferences({"pregeneration_start_hour": 25})

    def test_a_limit_outside_the_range_is_refused(self):
        with self.assertRaises(RequestError):
            validate_preferences({"pregeneration_max_per_run": 0})

    def test_a_window_that_wraps_past_midnight_is_accepted(self):
        validate_preferences({"pregeneration_start_hour": 23, "pregeneration_end_hour": 5})


class SavedSettingTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.scene_backlog.policy = DEPLOYMENT

    def _save(self, running, **values):
        return running.client.put("/api/v1/settings", json={"preferences": values})

    def _readiness(self, running) -> dict:
        response = running.client.get("/api/v1/scene-backlog/production-readiness")
        assert response.status_code == 200, response.text
        return response.json()

    def test_saving_the_setting_changes_what_the_runner_does_next_pass(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            user_id = running.client.get("/api/v1/session").json()["user_id"]

            before = running.services.scene_backlog.owner_policy(user_id)
            saved = self._save(running, pregeneration_enabled=False)
            after = running.services.scene_backlog.owner_policy(user_id)

            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(before.enabled)
            # The runner reads this, not the environment, so the next pass sees
            # the change rather than the next restart.
            self.assertFalse(after.enabled)

    def test_switching_it_off_stops_the_next_pass(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            user_id = running.client.get("/api/v1/session").json()["user_id"]
            self._save(running, pregeneration_enabled=False)

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)

            self.assertEqual(outcome["started"], [])
            self.assertIn("switched off", outcome["reason"])

    def test_switching_it_off_stops_production_that_was_running(self):
        """The strong version: it was making pictures, and then it was not."""

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            user_id = running.client.get("/api/v1/session").json()["user_id"]
            running.services.providers.media_providers["local-image"] = _Provider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Avery"},
            ).json()
            for index in range(2):
                entry = running.client.post(
                    "/api/v1/scene-backlog",
                    json={
                        "persona_id": persona["id"],
                        "scene": {"subject": "avery", "action": f"reading book {index}", "setting": "a room"},
                    },
                ).json()
                running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"})

            working = running.services.scene_backlog.produce_due(user_id, hour=3)
            for frame in working["started"]:
                running.wait_job(frame["job_id"])
            self.assertTrue(working["started"], working)

            self._save(running, pregeneration_enabled=False)
            after = running.services.scene_backlog.produce_due(user_id, hour=3)

            self.assertEqual(after["started"], [])
            self.assertIn("switched off", after["reason"])

    def test_a_saved_window_is_what_readiness_reports(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._save(running, pregeneration_start_hour=23, pregeneration_end_hour=5)

            readiness = self._readiness(running)

            self.assertEqual(readiness["window"], "23:00-05:00")
            self.assertEqual((readiness["start_hour"], readiness["end_hour"]), (23, 5))

    def test_an_impossible_window_is_refused_when_saved(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            refused = self._save(running, pregeneration_start_hour=4, pregeneration_end_hour=4)

            self.assertEqual(refused.status_code, 422, refused.text)
            # Nothing stored, so nothing to discover later.
            self.assertEqual(self._readiness(running)["window"], "02:00-06:00")

    def test_a_deployment_refusal_is_reported_rather_than_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.scene_backlog.policy = FORBIDDEN
            self._save(running, pregeneration_enabled=True)

            readiness = self._readiness(running)

            self.assertTrue(readiness["deployment_forbids"])
            self.assertFalse(readiness["enabled"])


if __name__ == "__main__":
    unittest.main()
