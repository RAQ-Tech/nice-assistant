"""One idea, several frames, generated as a set rather than as several pictures.

The shared scene is stored once so wardrobe, room, and lighting cannot drift
between frames, and each frame's seed follows from the set's base seed rather
than being random, so the set is reproducible and a frame can be remade as the
same picture. See ADR 0030.
"""

from pathlib import Path
import tempfile
import threading
import unittest

from app.photo_set import FRAME_FIELDS, frame_scene, frame_seed, normalize_definition, set_state
from app.provider_contracts import MediaArtifact
from tests.support import TestApp


SCENE = {
    "subject": "avery with dark hair",
    "action": "standing",
    "setting": "a lamplit room",
    "wardrobe": "an oversized jumper",
    "framing": "",
    "lighting": "warm lamplight",
    "camera": "",
    "mood": "quiet",
}
VARIATIONS = [
    {"action": "reading on the sofa", "framing": "full body"},
    {"action": "looking out of the window", "framing": "waist up", "mood": "thoughtful"},
    {"action": "curled up with a mug", "camera": "35mm"},
]


class RecordingImageProvider:
    name = "local-image"

    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        with self.lock:
            self.requests.append(request)
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class PhotoSetRecordTests(unittest.TestCase):
    """The pure part: what a set is, and what a frame may change."""

    def test_a_frame_may_change_pose_and_angle_but_not_wardrobe(self):
        composed = frame_scene(SCENE, {"action": "reading", "wardrobe": "a red dress"})

        self.assertEqual(composed["action"], "reading")
        # Wardrobe belongs to the set. A frame that could change it would be a
        # different picture, which is what a set exists to avoid.
        self.assertEqual(composed["wardrobe"], "an oversized jumper")

    def test_the_shared_scene_survives_a_frame_that_says_nothing(self):
        composed = frame_scene(SCENE, {})

        for field in ("subject", "setting", "wardrobe", "lighting"):
            self.assertEqual(composed[field], SCENE[field])

    def test_frames_are_only_allowed_to_change_the_declared_fields(self):
        definition = normalize_definition({"scene": SCENE, "variations": [{"wardrobe": "a red dress", "action": "x"}]})

        # The disallowed field is dropped rather than the frame refused: the
        # useful part of the request is usually the pose.
        self.assertEqual(set(definition["variations"][0]), {"action"})
        self.assertTrue(all(set(item) <= set(FRAME_FIELDS) for item in definition["variations"]))

    def test_seeds_follow_from_the_base_rather_than_being_random(self):
        self.assertEqual([frame_seed(1000, index) for index in range(3)], [1000, 1001, 1002])

    def test_one_frame_is_not_a_set(self):
        definition = normalize_definition({"scene": SCENE, "variations": [{"action": "reading"}]})

        self.assertTrue(any("at least" in reason for reason in definition["reasons"]))

    def test_a_set_where_no_frame_differs_is_refused(self):
        definition = normalize_definition({"scene": SCENE, "variations": [{}, {}]})

        self.assertTrue(any("differ" in reason for reason in definition["reasons"]))

    def test_a_set_needs_a_scene(self):
        definition = normalize_definition({"scene": {}, "variations": VARIATIONS})

        self.assertTrue(any("shared by every frame" in reason for reason in definition["reasons"]))

    def test_a_partly_made_set_says_partial_rather_than_done(self):
        self.assertEqual(set_state(3, 3, finished=True), "done")
        self.assertEqual(set_state(3, 2, finished=True), "partial")
        self.assertEqual(set_state(3, 2, finished=False), "generating")
        self.assertEqual(set_state(3, 0, finished=True), "planned")


class PhotoSetProductionTests(unittest.TestCase):
    def _ready(self, running):
        provider = RecordingImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = running.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace["id"], "name": "Avery"},
        ).json()
        return provider, persona

    def _create(self, running, persona_id, variations=None):
        response = running.client.post(
            "/api/v1/photo-sets",
            json={
                "persona_id": persona_id,
                "scene": SCENE,
                "variations": VARIATIONS if variations is None else variations,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def _produce(self, running, set_id):
        response = running.client.post(f"/api/v1/photo-sets/{set_id}/production")
        assert response.status_code == 200, response.text
        started = response.json()["started"]
        for frame in started:
            running.wait_job(frame["job_id"])
        return started

    def test_a_set_generates_every_frame_and_reports_itself_done(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            provider, persona = self._ready(running)
            created = self._create(running, persona["id"])

            self.assertEqual(created["state"], "planned")
            self.assertEqual(created["frame_count"], 3)
            started = self._produce(running, created["id"])

            self.assertEqual(len(started), 3)
            self.assertEqual(len(provider.requests), 3)
            final = running.client.get(f"/api/v1/photo-sets/{created['id']}").json()
            self.assertEqual(final["state"], "done")
            self.assertEqual(final["frames_done"], 3)
            self.assertEqual(final["frames_missing"], 0)

    def test_every_frame_links_back_to_the_set_that_made_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            self._produce(running, created["id"])

            final = running.client.get(f"/api/v1/photo-sets/{created['id']}").json()

            self.assertEqual([frame["frame_index"] for frame in final["frames"]], [0, 1, 2])
            self.assertEqual(
                [frame["seed"] for frame in final["frames"]],
                [frame_seed(final["base_seed"], index) for index in range(3)],
            )

    def test_the_shared_scene_reaches_every_frame_and_the_pose_does_not(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            self._produce(running, created["id"])

            prompts = [str(request.options["local_settings"]["compiled_prompt"]) for request in provider.requests]

            # Every frame carries the set's wardrobe and room.
            for prompt in prompts:
                self.assertIn("oversized jumper", prompt)
                self.assertIn("lamplit room", prompt)
            # And each frame carries only its own action.
            self.assertEqual(sum("reading on the sofa" in prompt for prompt in prompts), 1)
            self.assertEqual(sum("looking out of the window" in prompt for prompt in prompts), 1)

    def test_each_frame_is_generated_with_its_own_seed(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            self._produce(running, created["id"])

            seeds = sorted(request.options["local_settings"]["seed"] for request in provider.requests)

            self.assertEqual(seeds, [frame_seed(created["base_seed"], index) for index in range(3)])

    def test_the_journal_records_which_set_and_frame_produced_a_picture(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            self._produce(running, created["id"])

            final = running.client.get(f"/api/v1/photo-sets/{created['id']}").json()
            media_id = final["frames"][0]["media_id"]
            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            request_stage = next(stage for stage in journal["stages"] if stage["stage"] == "request")

            self.assertEqual(request_stage["detail"]["photo_set_id"], created["id"])
            self.assertIsNotNone(request_stage["detail"]["frame_index"])

    def test_a_set_cannot_be_produced_twice_at_once(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            self._produce(running, created["id"])

            again = running.client.post(f"/api/v1/photo-sets/{created['id']}/production")

            self.assertEqual(again.status_code, 409, again.text)

    def test_a_set_that_is_being_made_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            running.client.post(f"/api/v1/photo-sets/{created['id']}/production")

            refused = running.client.delete(f"/api/v1/photo-sets/{created['id']}")

            self.assertEqual(refused.status_code, 409, refused.text)

    def test_a_set_is_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _provider, persona = self._ready(running)
            created = self._create(running, persona["id"])
            running.client.delete("/api/v1/session")
            running.create_and_login("other")

            self.assertEqual(running.client.get(f"/api/v1/photo-sets/{created['id']}").status_code, 404)
            self.assertEqual(running.client.get("/api/v1/photo-sets").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
