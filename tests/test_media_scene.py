"""The typed scene the Task Model emits instead of prompt text.

Prompt syntax belongs to the checkpoint, so asking a small local model to write
finished prompt text asks it to make a decision it cannot make consistently. A
scene says what the picture is of; rendering is the compiler's job. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.media_scene import (
    SCENE_FIELDS,
    normalize_scene,
    render_scene,
    scene_is_empty,
    scene_summary,
)
from app.prompt_dialect import compile_prompt
from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING, CapabilityPlanningTaskInput, task_definition
from app.task_contracts import AvailableCapability, TaskContractError
from tests.support import FakeChatProvider, TestApp


SCENE = {
    "subject": "a woman with dark hair",
    "action": "walking a small dog",
    "setting": "a park at golden hour",
    "wardrobe": "a yellow raincoat",
    "framing": "full body",
    "lighting": "warm backlight",
    "camera": "35mm",
    "mood": "cheerful",
}


class SceneRecordTests(unittest.TestCase):
    def test_every_field_is_present_trimmed_and_bounded(self):
        scene = normalize_scene({"subject": "  a   cat  ", "action": "x" * 500, "unknown": "ignored"})
        self.assertEqual(set(scene), set(SCENE_FIELDS))
        self.assertEqual(scene["subject"], "a cat")
        self.assertEqual(len(scene["action"]), 200)
        self.assertEqual(scene["setting"], "")

    def test_an_absent_or_empty_scene_is_recognised_as_empty(self):
        self.assertTrue(scene_is_empty(None))
        self.assertTrue(scene_is_empty(normalize_scene({})))
        self.assertFalse(scene_is_empty(SCENE))

    def test_rendering_follows_the_declared_field_order(self):
        rendered = render_scene(SCENE)
        self.assertTrue(rendered.startswith("a woman with dark hair, walking a small dog"))
        self.assertTrue(rendered.endswith("cheerful"))
        # Empty fields leave no stray separators behind.
        self.assertNotIn(", ,", render_scene({**SCENE, "wardrobe": ""}))

    def test_the_summary_is_a_short_human_line_not_the_whole_scene(self):
        self.assertEqual(
            scene_summary(SCENE),
            "a woman with dark hair, walking a small dog in a park at golden hour",
        )
        self.assertEqual(scene_summary({"subject": "a cat"}), "a cat")
        self.assertEqual(scene_summary({}), "")


class SceneContractTests(unittest.TestCase):
    def _input(self) -> CapabilityPlanningTaskInput:
        return CapabilityPlanningTaskInput(
            user_text="Show me a portrait.",
            available_capabilities=(AvailableCapability("media.generate_image", "Generate image", "Create an image."),),
        )

    def test_the_model_is_asked_for_a_scene_and_not_for_prompt_text(self):
        schema = task_definition(CAPABILITY_PLANNING).response_schema(self._input())
        properties = schema["properties"]["requests"]["items"]["properties"]
        self.assertNotIn("prompt", properties)
        self.assertEqual(set(properties["scene"]["required"]), set(SCENE_FIELDS))
        self.assertFalse(properties["scene"]["additionalProperties"])

    def test_a_scene_with_nothing_in_it_is_refused(self):
        definition = task_definition(CAPABILITY_PLANNING)
        empty = ",".join(f'"{field}":""' for field in SCENE_FIELDS)
        with self.assertRaises(TaskContractError):
            definition.parse_output(
                '{"requests":[{"capability_key":"media.generate_image","scene":{' + empty + "},"
                '"operation":"generate","domains":[],"content_tags":[],"required_features":[],'
                '"persona_subject":false}]}',
                self._input(),
                384,
            )

    def test_the_model_still_cannot_name_a_resource_or_setting(self):
        definition = task_definition(CAPABILITY_PLANNING)
        for injected in ('"model":"forced"', '"workflow":"hands.json"', '"steps":40'):
            with self.assertRaises(TaskContractError):
                definition.parse_output(
                    '{"requests":[{"capability_key":"media.generate_image",'
                    '"scene":{"subject":"a portrait","action":"","setting":"","wardrobe":"",'
                    '"framing":"","lighting":"","camera":"","mood":""},'
                    '"operation":"generate","domains":[],"content_tags":[],"required_features":[],'
                    '"persona_subject":false,' + injected + "}]}",
                    self._input(),
                    384,
                )


class SceneCompilationTests(unittest.TestCase):
    def test_a_scene_is_rendered_into_the_dialect_rather_than_the_summary(self):
        compiled = compile_prompt("ignored request text", None, scene=SCENE)
        self.assertTrue(compiled["from_scene"])
        self.assertIn("a yellow raincoat", compiled["positive"])
        self.assertIn("35mm", compiled["positive"])
        self.assertNotIn("ignored request text", compiled["positive"])

    def test_a_direct_request_still_uses_the_words_the_user_typed(self):
        compiled = compile_prompt("a lighthouse in a storm", None, scene=None)
        self.assertFalse(compiled["from_scene"])
        self.assertIn("a lighthouse in a storm", compiled["positive"])

    def test_the_same_scene_renders_differently_per_dialect(self):
        booru = compile_prompt(
            "",
            {"style": "booru", "prefix": "score_9", "negative_prompt": "", "supports_negative": True},
            scene=SCENE,
        )
        default = compile_prompt("", None, scene=SCENE)
        self.assertNotEqual(booru["positive"], default["positive"])
        self.assertTrue(booru["positive"].startswith("score_9"))


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class SceneJournalTests(unittest.TestCase):
    def test_the_scene_that_produced_a_picture_is_recorded_with_it(self):
        planned = {
            "capability_key": "media.generate_image",
            "scene": SCENE,
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": False,
        }
        provider = FakeChatProvider(
            ["Here you go."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            chat = running.client.post("/api/v1/chats", json={"title": "Park", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a picture of you walking the dog", "memory_mode": "off"},
            ).json()
            chat_job = running.wait_job(accepted["job"]["id"])
            followup = (chat_job.get("result") or {}).get("followup_job_id")
            if followup:
                running.wait_job(followup)
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertTrue(requests)
            media_id = running.wait_job(requests[0]["job_id"])["result"]["mediaId"]

            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            stage = next(item for item in journal["stages"] if item["stage"] == "prompt_compiled")
            self.assertTrue(stage["detail"]["from_scene"])
            self.assertEqual(stage["detail"]["scene"]["wardrobe"], "a yellow raincoat")
            self.assertIn("a yellow raincoat", stage["detail"]["positive"])


if __name__ == "__main__":
    unittest.main()
