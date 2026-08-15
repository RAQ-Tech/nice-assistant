"""The retained picture library, and serving from it.

A picture that already exists arrives now; a better one that takes forty seconds
arrives after the conversation has moved on. Matching is over the scene record
rather than prompt text, because two prompts can describe the same picture and
comparing rendered strings would either miss that or match things nobody can
explain. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.media_library_service import MATCH_THRESHOLD, scene_similarity
from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


def scene(**overrides) -> dict:
    base = {
        "subject": "avery with dark hair",
        "action": "walking a small dog",
        "setting": "a park at golden hour",
        "wardrobe": "a yellow raincoat",
        "framing": "full body",
        "lighting": "warm backlight",
        "camera": "35mm",
        "mood": "cheerful",
    }
    base.update(overrides)
    return base


def planned(current: dict) -> dict:
    return {
        "capability_key": "media.generate_image",
        "scene": current,
        "operation": "generate",
        "domains": [],
        "content_tags": [],
        "required_features": [],
        "persona_subject": False,
    }


class RecordingImageProvider:
    name = "local-image"

    def __init__(self):
        self.requests = []

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class SceneMatchingTests(unittest.TestCase):
    def test_a_different_subject_never_matches_however_similar_the_rest(self):
        other = scene(subject="roofus the dog")
        # Everything else is identical, which is exactly the case a naive
        # string comparison would get wrong.
        self.assertEqual(scene_similarity(scene(), other), 0)

    def test_the_same_picture_described_the_same_way_matches_strongly(self):
        self.assertGreaterEqual(scene_similarity(scene(), scene()), MATCH_THRESHOLD)

    def test_a_request_asking_for_more_than_the_stored_picture_scores_lower(self):
        sparse = scene(wardrobe="", lighting="", camera="", mood="")
        self.assertLess(scene_similarity(scene(), sparse), scene_similarity(scene(), scene()))

    def test_an_empty_scene_on_either_side_matches_nothing(self):
        self.assertEqual(scene_similarity({}, scene()), 0)
        self.assertEqual(scene_similarity(scene(), {}), 0)


class LibraryServingTests(unittest.TestCase):
    def _ready(self, running) -> RecordingImageProvider:
        provider = RecordingImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return provider

    def _chat(self, running, title: str) -> dict:
        return running.client.post("/api/v1/chats", json={"title": title, "memory_mode": "off"}).json()

    def _turn(self, running, chat_id: str, text: str) -> str:
        accepted = running.client.post(
            f"/api/v1/chats/{chat_id}/turns",
            json={"text": text, "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        followup = (chat_job.get("result") or {}).get("followup_job_id")
        if followup:
            running.wait_job(followup)
        requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat_id}).json()["items"]
        assert requests, "no capability request was created"
        return running.wait_job(requests[0]["job_id"])["result"]["mediaId"]

    def test_a_generated_picture_is_retained_with_its_scene(self):
        provider = FakeChatProvider(["Here."], task_outputs={CAPABILITY_PLANNING: {"requests": [planned(scene())]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            chat = self._chat(running, "Park")
            media_id = self._turn(running, chat["id"], "Send me a picture of the park")

            entries = running.client.get("/api/v1/media-library").json()["items"]
            self.assertEqual([item["media_id"] for item in entries], [media_id])
            self.assertEqual(entries[0]["scene"]["wardrobe"], "a yellow raincoat")
            self.assertEqual(entries[0]["state"], "ready")

    def test_a_matching_request_in_another_conversation_is_served_without_generating(self):
        provider = FakeChatProvider(["Here."], task_outputs={CAPABILITY_PLANNING: {"requests": [planned(scene())]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            image_provider = self._ready(running)
            first = self._chat(running, "Park")
            original = self._turn(running, first["id"], "Send me a picture of the park")
            self.assertEqual(len(image_provider.requests), 1)

            second = self._chat(running, "Park again")
            served = self._turn(running, second["id"], "Send me a picture of the park")

            # No second provider call: the picture already existed.
            self.assertEqual(len(image_provider.requests), 1)
            self.assertEqual(served, original)

            journal = running.client.get(f"/api/v1/media/{served}/journal").json()
            self.assertIn("served_from_library", [item["stage"] for item in journal["stages"]])

    def test_a_picture_is_never_recycled_into_the_conversation_that_made_it(self):
        provider = FakeChatProvider(["Here."], task_outputs={CAPABILITY_PLANNING: {"requests": [planned(scene())]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            image_provider = self._ready(running)
            chat = self._chat(running, "Park")
            self._turn(running, chat["id"], "Send me a picture of the park")
            self._turn(running, chat["id"], "Send me another picture of the park")

            # Asking twice in one conversation means two pictures, not the same
            # one returned again.
            self.assertEqual(len(image_provider.requests), 2)

    def test_a_different_subject_generates_rather_than_serving(self):
        outputs = {CAPABILITY_PLANNING: {"requests": [planned(scene())]}}
        provider = FakeChatProvider(["Here."], task_outputs=outputs)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            image_provider = self._ready(running)
            first = self._chat(running, "Park")
            self._turn(running, first["id"], "Send me a picture of the park")

            outputs[CAPABILITY_PLANNING] = {"requests": [planned(scene(subject="roofus the dog"))]}
            provider.task_outputs = outputs
            second = self._chat(running, "Dog")
            self._turn(running, second["id"], "Send me a picture of the dog")

            self.assertEqual(len(image_provider.requests), 2)


class LibraryOperatorTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = RecordingImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _media(self, running) -> str:
        started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a harbour"})
        return running.wait_job(started.json()["job_id"])["result"]["mediaId"]

    def test_a_picture_can_be_added_by_hand_with_a_description(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            media_id = self._media(running)

            created = running.client.post(
                "/api/v1/media-library",
                json={"media_id": media_id, "scene": {"subject": "a harbour at dusk"}},
            )
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["scene"]["subject"], "a harbour at dusk")

    def test_a_picture_with_no_description_cannot_be_added(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            media_id = self._media(running)
            refused = running.client.post("/api/v1/media-library", json={"media_id": media_id, "scene": {}})
            # Without a description there is nothing to match it against later.
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("describe the picture", refused.text)

    def test_entries_can_be_removed_and_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            media_id = self._media(running)
            entry = running.client.post(
                "/api/v1/media-library",
                json={"media_id": media_id, "scene": {"subject": "a harbour at dusk"}},
            ).json()

            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            self.assertEqual(running.client.get("/api/v1/media-library").json()["items"], [])
            self.assertEqual(running.client.delete(f"/api/v1/media-library/{entry['id']}").status_code, 404)

            running.client.delete("/api/v1/session")
            running.client.post("/api/v1/session", json={"username": "owner", "password": "pass1234"})
            self.assertEqual(running.client.delete(f"/api/v1/media-library/{entry['id']}").status_code, 204)
            self.assertEqual(running.client.get("/api/v1/media-library").json()["items"], [])

    def test_the_library_is_capped_and_retires_the_oldest(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            running.services.media_library.entry_limit = 2
            for index in range(3):
                media_id = self._media(running)
                running.client.post(
                    "/api/v1/media-library",
                    json={"media_id": media_id, "scene": {"subject": f"harbour {index}"}},
                )

            states = [item["state"] for item in running.client.get("/api/v1/media-library").json()["items"]]
            # Retired, not deleted: the picture is still the owner's.
            self.assertEqual(states.count("retired"), 1)
            self.assertEqual(states.count("ready"), 2)


if __name__ == "__main__":
    unittest.main()
