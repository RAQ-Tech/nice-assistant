"""The routing tester.

Authoring a routing card is otherwise guesswork: there is no way to see whether
the sentence you wrote makes the preset you meant win. This runs the real
shortlist, the real Task Model role, and the real planner, so what it reports is
what would happen. It is deliberately temporary tooling. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact, ProviderError
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


SCENE = {
    "subject": "a manicure in pastel colours",
    "action": "",
    "setting": "",
    "wardrobe": "",
    "framing": "",
    "lighting": "",
    "camera": "",
    "mood": "",
}


def planned(preset: str = "") -> dict:
    request = {
        "capability_key": "media.generate_image",
        "scene": SCENE,
        "operation": "generate",
        "domains": [],
        "content_tags": [],
        "required_features": [],
        "persona_subject": False,
    }
    if preset:
        request["preset"] = preset
    return request


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class RoutingTesterTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _hand_preset(self, running) -> dict:
        running.client.get("/api/v1/media-catalog/presets")
        catalog = running.client.get("/api/v1/media-catalog").json()
        model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
        created = running.client.post(
            "/api/v1/media-catalog/presets",
            json={
                "name": "Hand detail",
                "priority": 1,
                "routing_card": "Use when hands or nails are the point of the picture.",
                "definition": {"base_model_resource_id": model["id"]},
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _preview(self, running, text: str) -> dict:
        response = running.client.post("/api/v1/media-catalog/routing-previews", json={"text": text})
        assert response.status_code == 200, response.text
        return response.json()

    def test_it_reports_the_shortlist_the_model_was_offered(self):
        provider = FakeChatProvider(["ok"], task_outputs={CAPABILITY_PLANNING: {"requests": [planned("preset_2")]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            self._hand_preset(running)
            preview = self._preview(running, "Send me a picture of my nails")

            titles = [item["title"] for item in preview["shortlist"]]
            self.assertIn("Hand detail", titles)
            card = next(item for item in preview["shortlist"] if item["title"] == "Hand detail")
            self.assertIn("hands or nails", card["routing_card"])
            # A shortlist entry must never carry resource identity.
            self.assertNotIn("resource_id", card)

    def test_it_shows_which_preset_won_and_who_chose_it(self):
        provider = FakeChatProvider(["ok"], task_outputs={CAPABILITY_PLANNING: {"requests": [planned("preset_2")]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            self._hand_preset(running)
            preview = self._preview(running, "Send me a picture of my nails")

            self.assertTrue(preview["requested"])
            self.assertTrue(preview["task_model"]["ran"])
            self.assertEqual(preview["task_model"]["chose"], "preset_2")
            self.assertEqual(preview["plan"]["explanation"]["preset"]["name"], "Hand detail")
            self.assertEqual(preview["plan"]["explanation"]["preset"]["source"], "task_model")

    def test_it_says_plainly_when_no_image_would_be_requested(self):
        provider = FakeChatProvider(["ok"], task_outputs={CAPABILITY_PLANNING: {"requests": []}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            preview = self._preview(running, "What did you do today?")

            self.assertFalse(preview["requested"])
            self.assertIsNone(preview["plan"])
            self.assertIn("did not request an image", preview["task_model"]["error"])

    def test_a_failing_task_model_is_reported_rather_than_hidden(self):
        provider = FakeChatProvider(
            ["ok"],
            task_errors={
                CAPABILITY_PLANNING: ProviderError(
                    provider="ollama", code="unavailable", user_message="Task model unavailable."
                )
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            preview = self._preview(running, "Send me a picture of my nails")

            # Not routing at all is the most common reason a preset never wins,
            # so the tester must never swallow it.
            self.assertFalse(preview["task_model"]["ran"])
            self.assertIn("fallback", preview["task_model"]["error"].casefold())

    def test_an_empty_message_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            refused = running.client.post("/api/v1/media-catalog/routing-previews", json={"text": "   "})
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("enter a message", refused.text)

    def test_the_preview_is_owner_scoped(self):
        provider = FakeChatProvider(["ok"], task_outputs={CAPABILITY_PLANNING: {"requests": [planned()]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            self._hand_preset(running)
            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            preview = self._preview(running, "Send me a picture of my nails")
            self.assertEqual(preview["shortlist"], [])


if __name__ == "__main__":
    unittest.main()
