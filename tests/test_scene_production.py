"""Making approved scenes while nobody is using the machine.

The policy decides whether a background picture may start; this covers what
happens when it does. A background picture goes through the same request, plan,
and journal a conversational one does, queues as bulk work so a requested
picture is always chosen first, and never leaves a scene claiming to be in
production when it is not. See ADR 0030.
"""

from pathlib import Path
import tempfile
import threading
import unittest

from app.database import initialize_database
from app.pregeneration import PregenerationPolicy
from app.provider_contracts import MediaArtifact
from app.scene_production import SceneProductionRunner
from tests.support import FakeChatProvider, TestApp


SCENE = {
    "subject": "avery with dark hair",
    "action": "reading on a sofa",
    "setting": "a lamplit room",
    "wardrobe": "an oversized jumper",
    "framing": "",
    "lighting": "",
    "camera": "",
    "mood": "quiet",
}


class BlockingImageProvider:
    """An image provider that can be held mid-generation, on purpose."""

    name = "local-image"

    def __init__(self, *, blocked=False, error=None):
        self.requests = []
        self.error = error
        self.release = threading.Event()
        self.started = threading.Event()
        if not blocked:
            self.release.set()

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=10)
        if self.error:
            raise self.error
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class SceneProductionTests(unittest.TestCase):
    def _ready(self, running, *, provider=None, hours=(0, 23), max_per_run=3):
        provider = provider or BlockingImageProvider()
        user_id = running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        running.services.scene_backlog.policy = PregenerationPolicy(
            enabled=True,
            start_hour=hours[0],
            end_hour=hours[1],
            max_per_run=max_per_run,
        )
        return user_id, provider

    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace["id"], "name": "Avery"},
        ).json()

    def _approved_scene(self, running, persona_id: str, scene=None) -> dict:
        entry = running.client.post(
            "/api/v1/scene-backlog",
            json={"persona_id": persona_id, "scene": SCENE if scene is None else scene},
        ).json()
        approved = running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"})
        assert approved.status_code == 200, approved.text
        return entry

    def _entry(self, running, entry_id: str) -> dict:
        items = running.client.get("/api/v1/scene-backlog").json()["items"]
        return next(item for item in items if item["id"] == entry_id)

    # -- producing ---------------------------------------------------------

    def test_an_approved_scene_is_made_and_recorded_against_its_entry(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, provider = self._ready(running)
            persona = self._persona(running)
            entry = self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            self.assertEqual(len(outcome["started"]), 1, outcome)
            running.wait_job(outcome["started"][0]["job_id"])

            settled = self._entry(running, entry["id"])
            self.assertEqual(settled["state"], "done")
            self.assertTrue(settled["media_id"])
            self.assertEqual(len(provider.requests), 1)

    def test_a_background_picture_is_kept_in_the_library_it_was_made_for(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, _ = self._ready(running)
            persona = self._persona(running)
            self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            running.wait_job(outcome["started"][0]["job_id"])

            entries = running.client.get("/api/v1/media-library").json()["items"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["scene"]["setting"], "a lamplit room")
            # Nothing served it into a conversation, because there is none.
            self.assertEqual(entries[0]["state"], "ready")

    def test_a_background_picture_keeps_a_journal_like_any_other(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, _ = self._ready(running)
            persona = self._persona(running)
            entry = self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            running.wait_job(outcome["started"][0]["job_id"])
            media_id = self._entry(running, entry["id"])["media_id"]

            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            self.assertEqual(journal["status"], "completed")
            self.assertIn("plan", [stage["stage"] for stage in journal["stages"]])

    def test_only_approved_scenes_are_made(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, provider = self._ready(running)
            persona = self._persona(running)
            # Proposed, never approved by anybody.
            running.client.post("/api/v1/scene-backlog", json={"persona_id": persona["id"], "scene": SCENE})

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)

            self.assertEqual(outcome["started"], [])
            self.assertIn("no approved scene", outcome["reason"])
            self.assertEqual(provider.requests, [])

    def test_production_stops_at_the_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, _ = self._ready(running, max_per_run=2)
            persona = self._persona(running)
            for index in range(4):
                self._approved_scene(running, persona["id"], scene={**SCENE, "action": f"reading book {index}"})

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)

            self.assertEqual(len(outcome["started"]), 2, outcome)

    # -- refusing ----------------------------------------------------------

    def test_nothing_is_made_outside_the_quiet_window(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, provider = self._ready(running, hours=(2, 6))
            persona = self._persona(running)
            self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=14)

            self.assertEqual(outcome["started"], [])
            self.assertIn("02:00-06:00", outcome["reason"])
            self.assertEqual(provider.requests, [])

    def test_nothing_is_made_while_production_is_switched_off(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, provider = self._ready(running)
            running.services.scene_backlog.policy = PregenerationPolicy(enabled=False)
            persona = self._persona(running)
            self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)

            self.assertIn("switched off", outcome["reason"])
            self.assertEqual(provider.requests, [])

    # -- not getting in the way -------------------------------------------

    def test_background_work_is_queued_as_bulk(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, provider = self._ready(running, provider=BlockingImageProvider(blocked=True))
            persona = self._persona(running)
            self._approved_scene(running, persona["id"])
            submitted = []
            original = running.services.jobs.submit

            def record(**values):
                submitted.append(values.get("latency_class"))
                return original(**values)

            running.services.jobs.submit = record
            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            provider.started.wait(timeout=5)
            provider.release.set()
            running.wait_job(outcome["started"][0]["job_id"])

            # Bulk is what makes a requested picture win inside the media lane.
            self.assertEqual(submitted, ["bulk"])

    def test_a_conversation_is_never_delayed_behind_a_background_picture(self):
        chat_provider = FakeChatProvider(["Still here."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            user_id, provider = self._ready(running, provider=BlockingImageProvider(blocked=True))
            persona = self._persona(running)
            self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            self.assertTrue(provider.started.wait(timeout=5), "background picture never started")

            # The picture is mid-generation and holding the media lane. A person
            # typing right now must not wait for it.
            chat = running.client.post("/api/v1/chats", json={"title": "Hello", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "are you there?", "memory_mode": "off"},
            ).json()
            finished = running.wait_job(accepted["job"]["id"], timeout=10)
            self.assertEqual(finished["status"], "completed")

            provider.release.set()
            running.wait_job(outcome["started"][0]["job_id"])

    # -- failing honestly --------------------------------------------------

    def test_a_failed_picture_returns_its_scene_to_the_queue(self):
        provider = BlockingImageProvider(error=RuntimeError("the graph exploded"))
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id, _ = self._ready(running, provider=provider)
            persona = self._persona(running)
            entry = self._approved_scene(running, persona["id"])

            outcome = running.services.scene_backlog.produce_due(user_id, hour=3)
            running.wait_job(outcome["started"][0]["job_id"])

            settled = self._entry(running, entry["id"])
            # Approved, not stranded in `generating`, and not falsely `done`.
            self.assertEqual(settled["state"], "approved")
            self.assertIsNone(settled["media_id"])

    def test_a_restart_returns_an_interrupted_picture_to_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with TestApp(base) as running:
                user_id, provider = self._ready(running, provider=BlockingImageProvider(blocked=True))
                persona = self._persona(running)
                entry = self._approved_scene(running, persona["id"])
                running.services.scene_backlog.produce_due(user_id, hour=3)
                provider.started.wait(timeout=5)
                self.assertEqual(self._entry(running, entry["id"])["state"], "generating")
                provider.release.set()
                database_path = running.config.data_dir / "app.db"

            # The process died mid-picture. Nothing is resumable, so the scene
            # goes back in the queue rather than claiming work nobody is doing.
            initialize_database(database_path, 3600)

            with TestApp(base) as restarted:
                restarted.client.post("/api/v1/session", json={"username": "owner", "password": "pass1234"})
                self.assertEqual(self._entry(restarted, entry["id"])["state"], "approved")


class ProductionRunnerTests(unittest.TestCase):
    """The loop that decides when to ask, and nothing else."""

    def test_it_does_not_start_a_thread_it_would_never_use(self):
        runner = SceneProductionRunner(object(), None, enabled=False)
        runner.start()
        self.assertIsNone(runner._thread)

    def test_one_owner_failing_does_not_stop_production_for_the_rest(self):
        class Backlog:
            def owners_with_work(self):
                return ["broken", "fine"]

            def produce_due(self, user_id):
                if user_id == "broken":
                    raise RuntimeError("its database went away")
                return {"started": [{"entry_id": "e1"}], "reason": "quiet"}

        results = SceneProductionRunner(Backlog(), None, enabled=True).run_once()

        self.assertEqual([item["user_id"] for item in results], ["fine"])

    def test_an_owner_that_started_nothing_still_reports_why(self):
        class Backlog:
            def owners_with_work(self):
                return ["owner"]

            def produce_due(self, user_id):
                return {"started": [], "reason": "a conversation is waiting"}

        results = SceneProductionRunner(Backlog(), None, enabled=True).run_once()

        self.assertEqual(results, [{"user_id": "owner", "started": [], "reason": "a conversation is waiting"}])


if __name__ == "__main__":
    unittest.main()
