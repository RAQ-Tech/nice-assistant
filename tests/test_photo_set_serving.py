"""Several frames of one set arriving together.

A set is only worth generating if it can arrive as a set. The frames sent are
the ones this conversation has not already seen, the number is bounded, and a
set that is only partly made can still be served from. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


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
    {"action": "reading on the sofa"},
    {"action": "looking out of the window"},
    {"action": "curled up with a mug"},
]


def planned(scene: dict) -> dict:
    return {
        "capability_key": "media.generate_image",
        "scene": scene,
        "operation": "generate",
        "domains": [],
        "content_tags": [],
        "required_features": [],
        "persona_subject": False,
    }


class CountingImageProvider:
    name = "local-image"

    def __init__(self):
        self.count = 0

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.count += 1
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class PhotoSetServingTests(unittest.TestCase):
    def _ready(self, running):
        provider = CountingImageProvider()
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

    def _make_set(self, running, persona_id, variations=None):
        created = running.client.post(
            "/api/v1/photo-sets",
            json={
                "persona_id": persona_id,
                "scene": SCENE,
                "variations": VARIATIONS if variations is None else variations,
            },
        ).json()
        started = running.client.post(f"/api/v1/photo-sets/{created['id']}/production").json()["started"]
        for frame in started:
            running.wait_job(frame["job_id"])
        return created

    def _ask(self, running, chat_id: str, text: str) -> dict:
        before = {
            item["id"]
            for item in running.client.get("/api/v1/capability-requests", params={"chat_id": chat_id}).json()["items"]
        }
        accepted = running.client.post(
            f"/api/v1/chats/{chat_id}/turns",
            json={"text": text, "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        for followup in (chat_job.get("result") or {}).get("followup_job_ids") or []:
            running.wait_job(followup)
        fresh = [
            item
            for item in running.client.get("/api/v1/capability-requests", params={"chat_id": chat_id}).json()["items"]
            if item["id"] not in before
        ]
        assert fresh, "no capability request was created"
        running.wait_job(fresh[0]["job_id"])
        return running.client.get(f"/api/v1/capability-requests/{fresh[0]['id']}").json()

    def _chat(self, running, title: str) -> str:
        return running.client.post("/api/v1/chats", json={"title": title, "memory_mode": "off"}).json()["id"]

    def test_a_request_matching_a_set_arrives_as_several_frames(self):
        chat_provider = FakeChatProvider(
            ["Here."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned({**SCENE, "action": "reading on the sofa"})]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            provider, persona = self._ready(running)
            self._make_set(running, persona["id"])
            generated_for_the_set = provider.count

            request = self._ask(running, self._chat(running, "Ask"), "Send me a picture of you reading")

            # Nothing new was generated: the set already existed.
            self.assertEqual(provider.count, generated_for_the_set)
            attachment = request["attachment"]
            self.assertTrue(attachment["media_id"])
            self.assertEqual(len(attachment["frames"]), 2)

    def test_the_number_of_frames_sent_at_once_is_bounded(self):
        many = [{"action": f"pose {index}"} for index in range(6)]
        chat_provider = FakeChatProvider(
            ["Here."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned({**SCENE, "action": "pose 0"})]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            _provider, persona = self._ready(running)
            self._make_set(running, persona["id"], variations=many)

            request = self._ask(running, self._chat(running, "Ask"), "Send me a picture of you")

            # Six frames exist. Three arrive, because a wall of pictures is not
            # an answer.
            self.assertEqual(len(request["attachment"]["frames"]), 2)

    def test_a_conversation_never_receives_the_same_frame_twice(self):
        chat_provider = FakeChatProvider(
            ["Here.", "Here again."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned({**SCENE, "action": "reading on the sofa"})]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            _provider, persona = self._ready(running)
            self._make_set(running, persona["id"])
            chat_id = self._chat(running, "Ask")

            first = self._ask(running, chat_id, "Send me a picture of you reading")
            second = self._ask(running, chat_id, "Send me another picture of you reading")

            first_media = {first["attachment"]["media_id"]} | {
                frame["media_id"] for frame in first["attachment"]["frames"]
            }
            second_media = {second["attachment"]["media_id"]} | {
                frame["media_id"] for frame in second["attachment"]["frames"]
            }
            self.assertFalse(first_media & second_media, "a frame was sent into this conversation twice")

    def test_a_partly_made_set_can_still_be_served_from(self):
        chat_provider = FakeChatProvider(
            ["Here."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned({**SCENE, "action": "reading on the sofa"})]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            _provider, persona = self._ready(running)
            created = self._make_set(running, persona["id"])
            # Retire one frame, leaving the set incomplete.
            entries = running.client.get("/api/v1/media-library").json()["items"]
            running.client.delete(f"/api/v1/media-library/{entries[0]['id']}")

            request = self._ask(running, self._chat(running, "Ask"), "Send me a picture of you reading")

            self.assertTrue(request["attachment"]["media_id"])
            self.assertGreaterEqual(len(request["attachment"]["frames"]), 1)
            self.assertEqual(
                running.client.get(f"/api/v1/photo-sets/{created['id']}").json()["frames_done"],
                2,
            )

    def test_an_ordinary_picture_still_arrives_alone(self):
        chat_provider = FakeChatProvider(
            ["Here."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned({**SCENE, "action": "reading on the sofa"})]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            self._ready(running)

            request = self._ask(running, self._chat(running, "Ask"), "Send me a picture of you reading")

            # Generated rather than served, and one picture rather than a set.
            self.assertEqual(request["attachment"]["frames"], [])


if __name__ == "__main__":
    unittest.main()
