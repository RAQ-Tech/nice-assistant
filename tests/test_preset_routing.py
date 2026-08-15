"""Routing a request to a preset.

The platform hard-filters what is legal, offers the task model a bounded
shortlist of opaque labels with the operator's own note about when each applies,
and falls back to the deterministic score whenever the model cannot answer
usefully. Resource identity never reaches the model. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact, ProviderError
from app.task_contracts import (
    CAPABILITY_PLANNING,
    AvailableCapability,
    AvailablePreset,
    CapabilityPlanningTaskInput,
    TaskContractError,
    task_definition,
)
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


class ShortlistContractTests(unittest.TestCase):
    def _input(self, presets=()) -> CapabilityPlanningTaskInput:
        return CapabilityPlanningTaskInput(
            user_text="Show me my nails.",
            available_capabilities=(AvailableCapability("media.generate_image", "Generate image", "Create an image."),),
            available_presets=presets,
        )

    def test_the_shortlist_offers_labels_and_routing_cards_only(self):
        presets = (AvailablePreset("preset_1", "Hand detail", "Use when hands or nails are the point."),)
        definition = task_definition(CAPABILITY_PLANNING)
        payload = definition.messages(self._input(presets))[1]["content"]

        self.assertIn("preset_1", payload)
        self.assertIn("Use when hands or nails are the point.", payload)
        # Nothing that identifies a resource may appear.
        self.assertNotIn("base_model_resource_id", payload)
        self.assertNotIn("safetensors", payload)

    def test_the_model_may_only_choose_a_label_it_was_offered(self):
        presets = (AvailablePreset("preset_1", "Hand detail", "Hands."),)
        definition = task_definition(CAPABILITY_PLANNING)
        enum = definition.response_schema(self._input(presets))["properties"]["requests"]["items"]["properties"][
            "preset"
        ]["enum"]
        self.assertEqual(enum, ["", "preset_1"])

        with self.assertRaises(TaskContractError):
            definition.parse_output(
                '{"requests":[{"capability_key":"media.generate_image","scene":'
                '{"subject":"nails","action":"","setting":"","wardrobe":"","framing":"",'
                '"lighting":"","camera":"","mood":""},"operation":"generate","domains":[],'
                '"content_tags":[],"required_features":[],"persona_subject":false,'
                '"preset":"preset_9"}]}',
                self._input(presets),
                384,
            )

    def test_no_shortlist_means_no_preset_field_at_all(self):
        definition = task_definition(CAPABILITY_PLANNING)
        properties = definition.response_schema(self._input())["properties"]["requests"]["items"]["properties"]
        self.assertNotIn("preset", properties)


class ShortlistRoutingTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _second_preset(self, running, *, name: str, priority: int) -> dict:
        # Read presets first so the backfilled one for the base model exists;
        # the backfill gives a model one preset, not one per operator recipe.
        running.client.get("/api/v1/media-catalog/presets")
        catalog = running.client.get("/api/v1/media-catalog").json()
        model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
        created = running.client.post(
            "/api/v1/media-catalog/presets",
            json={
                "name": name,
                "priority": priority,
                "routing_card": "Use when hands or nails are the point of the picture.",
                "definition": {"base_model_resource_id": model["id"]},
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _plan_for(self, running, chat_id: str) -> dict:
        requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat_id}).json()["items"]
        assert requests, "no capability request was created"
        return requests[0]["media_plan"]

    def _turn(self, running, chat_id: str, text: str) -> None:
        accepted = running.client.post(
            f"/api/v1/chats/{chat_id}/turns",
            json={"text": text, "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        followup = (chat_job.get("result") or {}).get("followup_job_id")
        if followup:
            running.wait_job(followup)

    def test_the_model_can_route_to_a_lower_priority_preset(self):
        provider = FakeChatProvider(
            ["Here you go."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned("preset_2")]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            # Deliberately the lowest priority, so only routing can select it.
            self._second_preset(running, name="Hand detail", priority=1)
            chat = running.client.post("/api/v1/chats", json={"title": "Nails", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "Send me a picture of my nails")

            plan = self._plan_for(running, chat["id"])
            self.assertEqual(plan["explanation"]["preset"]["source"], "task_model")
            self.assertEqual(plan["explanation"]["preset"]["name"], "Hand detail")

    def test_an_absent_choice_falls_back_to_the_deterministic_score(self):
        provider = FakeChatProvider(
            ["Here you go."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned()]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            self._second_preset(running, name="Hand detail", priority=1)
            chat = running.client.post("/api/v1/chats", json={"title": "Nails", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "Send me a picture of my nails")

            plan = self._plan_for(running, chat["id"])
            self.assertEqual(plan["explanation"]["preset"]["source"], "deterministic")
            # The highest-priority preset, not the one the model declined to pick.
            self.assertNotEqual(plan["explanation"]["preset"]["name"], "Hand detail")

    def test_a_failing_task_model_still_produces_a_plan(self):
        provider = FakeChatProvider(
            ["Here you go."],
            task_errors={
                CAPABILITY_PLANNING: ProviderError(
                    provider="ollama", code="unavailable", user_message="Task model unavailable."
                )
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "Nails", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "Send me a picture of my nails")

            plan = self._plan_for(running, chat["id"])
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["explanation"]["preset"]["source"], "deterministic")

    def test_the_plan_records_what_was_considered(self):
        provider = FakeChatProvider(
            ["Here you go."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned("preset_2")]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            self._second_preset(running, name="Hand detail", priority=1)
            chat = running.client.post("/api/v1/chats", json={"title": "Nails", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "Send me a picture of my nails")

            considered = self._plan_for(running, chat["id"])["explanation"]["preset"]["considered"]
            self.assertGreaterEqual(len(considered), 2)
            self.assertIn("Hand detail", [item["name"] for item in considered])


if __name__ == "__main__":
    unittest.main()
