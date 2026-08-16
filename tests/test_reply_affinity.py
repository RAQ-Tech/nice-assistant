"""The persona's own words picking between pictures that already qualify.

"Took Roofus for a walk" should not arrive beside a beach photo. The fix ranks
candidates the user's request already produced; it never adds one. See ADR 0033,
and ADR 0017 for the rule it deliberately does not weaken.
"""

from pathlib import Path
import tempfile
import unittest

from app.media_library_service import MATCH_THRESHOLD, reply_affinity, scene_similarity
from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp
from tests.test_planning_context import planning_payloads


def scene(**overrides) -> dict:
    base = {
        "subject": "avery with dark hair",
        "action": "",
        "setting": "",
        "wardrobe": "",
        "framing": "",
        "lighting": "",
        "camera": "",
        "mood": "",
    }
    base.update(overrides)
    return base


DOG = scene(action="walking roofus the dog", setting="a village lane")
BEACH = scene(action="paddling", setting="a beach at sunset")
ASKED = scene(subject="avery with dark hair")


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


class ImageProvider:
    name = "local-image"

    def __init__(self):
        self.count = 0

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.count += 1
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class AffinityTests(unittest.TestCase):
    def test_a_reply_matches_the_picture_it_describes(self):
        self.assertGreater(
            reply_affinity("I took Roofus for a walk down the lane", DOG),
            reply_affinity("I took Roofus for a walk down the lane", BEACH),
        )

    def test_no_reply_means_no_opinion(self):
        self.assertEqual(reply_affinity("", DOG), 0)

    def test_a_reply_about_nothing_in_the_picture_says_nothing(self):
        self.assertEqual(reply_affinity("Yes, absolutely", DOG), 0)

    def test_affinity_is_never_a_threshold(self):
        # It orders. Eligibility is decided entirely by the request's own scene,
        # which is what keeps ADR 0017 intact.
        self.assertLess(scene_similarity(ASKED, DOG), MATCH_THRESHOLD + 100)
        self.assertEqual(reply_affinity("walking roofus", scene()), 0)


class ReplyRankingTests(unittest.TestCase):
    def _ready(self, running):
        provider = ImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return provider

    def _turn(self, running, chat_id: str, text: str) -> str:
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
        return running.wait_job(fresh[0]["job_id"])["result"]["mediaId"]

    def _chat(self, running, title: str) -> str:
        return running.client.post("/api/v1/chats", json={"title": title, "memory_mode": "off"}).json()["id"]

    def _stock(self, running, provider):
        """Two retained pictures of one persona, made in one conversation.

        One chat on purpose: a picture is never served back into the chat that
        produced it, so the second request is genuinely generated rather than
        answered with the first. Both then qualify for a later, vaguer request
        made somewhere else, which is the situation the reply has to settle.
        """

        studio = self._chat(running, "Studio")
        outputs = {CAPABILITY_PLANNING: {"requests": [planned(DOG)]}}
        provider.task_outputs = outputs
        dog_media = self._turn(running, studio, "Send me a picture of you and the dog")
        outputs[CAPABILITY_PLANNING] = {"requests": [planned(BEACH)]}
        provider.task_outputs = outputs
        beach_media = self._turn(running, studio, "Send me a picture of you at the beach")
        return dog_media, beach_media, outputs

    def _mention(self, running, chat_id: str, text: str) -> None:
        """An ordinary turn: the persona says something, no picture involved."""

        accepted = running.client.post(
            f"/api/v1/chats/{chat_id}/turns",
            json={"text": text, "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        for followup in (chat_job.get("result") or {}).get("followup_job_ids") or []:
            running.wait_job(followup)

    def test_the_picture_matches_what_the_persona_has_been_saying(self):
        chat_provider = FakeChatProvider(
            ["Here.", "Here.", "I took Roofus for a walk down the lane today.", "Of course."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned(DOG)]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            image_provider = self._ready(running)
            dog_media, beach_media, outputs = self._stock(running, chat_provider)
            self.assertEqual(image_provider.count, 2)

            asking = self._chat(running, "Ask")
            outputs[CAPABILITY_PLANNING] = {"requests": []}
            chat_provider.task_outputs = outputs
            # The persona mentions the walk. This turn makes no picture.
            self._mention(running, asking, "How was your day?")

            # Then a picture is asked for, vaguely: both retained pictures clear
            # the threshold on subject alone, so what the persona said decides.
            outputs[CAPABILITY_PLANNING] = {"requests": [planned(ASKED)]}
            chat_provider.task_outputs = outputs
            served = self._turn(running, asking, "Send me a picture of you")

            self.assertEqual(image_provider.count, 2, "a picture was generated instead of served")
            self.assertEqual(served, dog_media)
            self.assertNotEqual(served, beach_media)

    def test_with_no_matching_picture_nothing_changes(self):
        chat_provider = FakeChatProvider(
            ["Here.", "Here.", "I took Roofus for a walk down the lane today.", "Of course."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned(DOG)]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            image_provider = self._ready(running)
            self._stock(running, chat_provider)
            asking = self._chat(running, "Other")
            outputs = {CAPABILITY_PLANNING: {"requests": []}}
            chat_provider.task_outputs = outputs
            self._mention(running, asking, "How was your day?")

            # A different subject entirely: what the persona said cannot rescue
            # it, because there is nothing eligible to rank.
            outputs = {CAPABILITY_PLANNING: {"requests": [planned(scene(subject="roofus the dog alone"))]}}
            chat_provider.task_outputs = outputs
            self._turn(running, asking, "Send me a picture of the dog on his own")

            self.assertEqual(image_provider.count, 3, "prose made an ineligible picture eligible")

    def test_the_guarded_reply_on_the_asking_turn_is_not_what_is_read(self):
        chat_provider = FakeChatProvider(
            ["Here.", "Here.", "I took Roofus for a walk down the lane today.", "Of course."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned(DOG)]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            self._ready(running)
            _dog, _beach, outputs = self._stock(running, chat_provider)
            asking = self._chat(running, "Ask")
            outputs[CAPABILITY_PLANNING] = {"requests": []}
            chat_provider.task_outputs = outputs
            self._mention(running, asking, "How was your day?")
            outputs[CAPABILITY_PLANNING] = {"requests": [planned(ASKED)]}
            chat_provider.task_outputs = outputs
            self._turn(running, asking, "Send me a picture of you")

            messages = running.client.get(f"/api/v1/chats/{asking}").json()["messages"]
            replies = [item["text"] for item in messages if item["role"] == "assistant"]

            # ADR 0021 replaces the persona's words on an explicit picture
            # request. That is why ranking reads the transcript rather than the
            # reply on this turn: there is nothing in it.
            self.assertIn("see what I can make", replies[-1])
            self.assertIn("Roofus", " ".join(replies))

    def test_persona_prose_still_never_reaches_planning(self):
        chat_provider = FakeChatProvider(
            ["Of course - here is a lovely portrait of me on a beach in a red dress."],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            self._ready(running)
            chat_id = self._chat(running, "Chat")
            accepted = running.client.post(
                f"/api/v1/chats/{chat_id}/turns",
                json={"text": "How was your day?", "memory_mode": "off"},
            ).json()
            chat_job = running.wait_job(accepted["job"]["id"])
            for followup in (chat_job.get("result") or {}).get("followup_job_ids") or []:
                running.wait_job(followup)

            payloads = str(planning_payloads(chat_provider))
            # ADR 0017 is unchanged: prose ranks pictures, it never reaches the
            # model that decides what to make.
            self.assertNotIn("red dress", payloads)
            self.assertNotIn("beach", payloads)


if __name__ == "__main__":
    unittest.main()
