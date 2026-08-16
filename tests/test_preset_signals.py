"""What happens to a picture after it is made, counted against its preset.

Only explicit signals: a picture deliberately kept, one sent again, one removed.
Generating a picture is not a signal, because the platform chose the preset and
counting that would be the platform scoring its own homework. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.preset_signals import PresetSignals, describe, preferred_order
from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
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


class RecordingImageProvider:
    name = "local-image"

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class WeightTests(unittest.TestCase):
    """The arithmetic, on its own."""

    def test_kept_and_sent_again_earn_a_point_each_and_removed_loses_one(self):
        self.assertEqual(PresetSignals("p", kept=2, sent_again=1, removed=1).weight, 2)

    def test_a_preset_whose_pictures_keep_being_removed_is_not_promoted(self):
        order = preferred_order([PresetSignals("bad", sent_again=5, removed=9), PresetSignals("good", kept=1)])

        # Used a lot, disliked every time. Volume is not evidence of quality.
        self.assertEqual(order, ["good"])

    def test_a_preset_nobody_has_judged_says_nothing(self):
        self.assertEqual(preferred_order([PresetSignals("quiet")]), [])

    def test_more_evidence_breaks_a_tie(self):
        order = preferred_order([PresetSignals("thin", kept=1), PresetSignals("thick", kept=3, removed=2)])

        # Both weigh 1. The one with more behind it goes first.
        self.assertEqual(order, ["thick", "thin"])

    def test_the_description_says_what_was_counted_not_what_was_learned(self):
        summary = describe(PresetSignals("p", kept=2, removed=1))

        self.assertEqual(summary, "2 kept, 1 removed")
        for forbidden in ("learn", "prefer", "like"):
            self.assertNotIn(forbidden, summary.casefold())

    def test_nothing_counted_says_so_plainly(self):
        self.assertIn("No pictures", describe(PresetSignals("p")))


class SignalRecordingTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = RecordingImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _generate(self, running, chat_title="Ask") -> str:
        chat = running.client.post("/api/v1/chats", json={"title": chat_title, "memory_mode": "off"}).json()
        before = {
            item["id"]
            for item in running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()[
                "items"
            ]
        }
        accepted = running.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Send me a picture of the room", "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        for followup in (chat_job.get("result") or {}).get("followup_job_ids") or []:
            running.wait_job(followup)
        fresh = [
            item
            for item in running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()[
                "items"
            ]
            if item["id"] not in before
        ]
        assert fresh, "no capability request was created"
        return running.wait_job(fresh[0]["job_id"])["result"]["mediaId"]

    def _signals(self, running) -> list[dict]:
        return running.client.get("/api/v1/preset-signals").json()["items"]

    def _provider(self, running):
        return FakeChatProvider(["Here."], task_outputs={CAPABILITY_PLANNING: {"requests": [planned(SCENE)]}})

    def test_generating_a_picture_is_not_a_signal(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running)

            # The platform chose the preset. Counting that would be the platform
            # scoring its own homework.
            self.assertEqual(self._signals(running), [])

    def test_sending_a_picture_again_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running, "First")
            # A second conversation asking for the same thing is answered from
            # the library, which is the picture earning its place.
            self._generate(running, "Second")

            signals = self._signals(running)

            self.assertEqual(len(signals), 1, signals)
            self.assertEqual(signals[0]["sent_again"], 1)
            self.assertEqual(signals[0]["weight"], 1)

    def test_removing_a_picture_is_counted_against_its_preset(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running)
            entry = running.client.get("/api/v1/media-library").json()["items"][0]

            running.client.delete(f"/api/v1/media-library/{entry['id']}")

            signals = self._signals(running)
            self.assertEqual(signals[0]["removed"], 1)
            self.assertEqual(signals[0]["weight"], -1)

    def test_the_counts_are_shown_beside_the_weight(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running, "First")
            self._generate(running, "Second")

            signals = self._signals(running)[0]

            self.assertTrue(signals["preset_name"])
            self.assertEqual(signals["summary"], "1 sent again")

    def test_counts_are_individually_resettable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running, "First")
            self._generate(running, "Second")
            preset_id = self._signals(running)[0]["preset_id"]

            cleared = running.client.delete(f"/api/v1/preset-signals/{preset_id}")

            self.assertEqual(cleared.status_code, 204, cleared.text)
            self.assertEqual(self._signals(running), [])

    def test_resetting_something_never_counted_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            self.assertEqual(running.client.delete("/api/v1/preset-signals/nothing").status_code, 404)

    def test_signals_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=self._provider(None)) as running:
            self._ready(running)
            self._generate(running, "First")
            self._generate(running, "Second")
            running.client.delete("/api/v1/session")
            running.create_and_login("other")

            self.assertEqual(self._signals(running), [])


if __name__ == "__main__":
    unittest.main()
