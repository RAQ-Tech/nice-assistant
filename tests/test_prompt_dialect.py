"""Per-model prompt dialect.

Prompt syntax belongs to the checkpoint, not to the request. These tests pin the
behavior that replaced one hardcoded quality prefix and one global negative
string applied to every local generation. See ADR 0030.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.media_clients import comfyui_image
from app.prompt_dialect import (
    DEFAULT_DIALECT,
    LEGACY_NEGATIVE,
    LEGACY_QUALITY_PREFIX,
    SAFETY_NEGATIVE,
    compile_prompt,
    normalize_dialect,
)
from app.provider_contracts import CancellationToken, MediaArtifact
from app.service_errors import RequestError
from tests.support import TestApp
from tests.test_workflow_bindings import comfy_transport


BOORU = {
    "style": "booru",
    "prefix": "score_9, score_8_up, score_7_up",
    "suffix": "source_photo",
    "negative_prompt": "score_4, score_5, worst quality",
    "supports_negative": True,
    "trigger_placement": "prefix",
    "target_length": 0,
}
FLUX = {
    "style": "natural_language",
    "prefix": "",
    "suffix": "",
    "negative_prompt": "",
    "supports_negative": False,
    "trigger_placement": "suffix",
    "target_length": 0,
}


class DialectValidationTests(unittest.TestCase):
    def test_an_unstated_dialect_reproduces_the_previous_behavior(self):
        compiled = compile_prompt("a harbour at dawn", None)
        self.assertTrue(compiled["positive"].startswith(LEGACY_QUALITY_PREFIX))
        self.assertEqual(compiled["negative"], LEGACY_NEGATIVE)

    def test_unknown_fields_and_bad_values_are_refused_by_name(self):
        with self.assertRaises(RequestError) as unknown:
            normalize_dialect({"styl": "booru"})
        self.assertIn("styl", str(unknown.exception))
        with self.assertRaises(RequestError):
            normalize_dialect({"style": "shakespearean"})
        with self.assertRaises(RequestError):
            normalize_dialect({"trigger_placement": "middle"})
        with self.assertRaises(RequestError):
            normalize_dialect({"supports_negative": "yes"})
        with self.assertRaises(RequestError):
            normalize_dialect({"target_length": -1})

    def test_normalizing_nothing_yields_the_documented_default(self):
        self.assertEqual(normalize_dialect(None), DEFAULT_DIALECT)
        self.assertEqual(normalize_dialect({}), DEFAULT_DIALECT)


class DialectCompilationTests(unittest.TestCase):
    def test_two_dialects_render_the_same_request_differently(self):
        booru = compile_prompt("a lighthouse in a storm", BOORU)
        default = compile_prompt("a lighthouse in a storm", None)

        self.assertNotEqual(booru["positive"], default["positive"])
        self.assertTrue(booru["positive"].startswith("score_9, score_8_up, score_7_up"))
        self.assertTrue(booru["positive"].endswith("source_photo"))
        self.assertNotIn(LEGACY_QUALITY_PREFIX, booru["positive"])
        self.assertIn("a lighthouse in a storm", booru["positive"])

    def test_a_dialect_that_takes_no_negative_prompt_sends_none(self):
        compiled = compile_prompt("a lighthouse", FLUX, allow_nsfw=False)
        self.assertEqual(compiled["negative"], "")
        self.assertFalse(compiled["supports_negative"])
        # A model with no negative prompt cannot carry the safety negative
        # either, and the record must not imply that it did.
        self.assertFalse(compiled["safety_negative_applied"])

    def test_the_platform_safety_negative_stays_separate_from_the_model_negative(self):
        allowed = compile_prompt("a portrait", BOORU, allow_nsfw=True)
        blocked = compile_prompt("a portrait", BOORU, allow_nsfw=False)

        self.assertEqual(allowed["negative"], BOORU["negative_prompt"])
        self.assertIn(BOORU["negative_prompt"], blocked["negative"])
        self.assertIn(SAFETY_NEGATIVE, blocked["negative"])
        self.assertTrue(blocked["safety_negative_applied"])

    def test_compilation_is_pure(self):
        first = compile_prompt("a red bicycle", BOORU, loras=[{"trigger_words": ["neon"]}])
        second = compile_prompt("a red bicycle", BOORU, loras=[{"trigger_words": ["neon"]}])
        self.assertEqual(first, second)

    def test_trigger_words_go_where_the_dialect_says(self):
        loras = [{"trigger_words": ["neon glow"]}]
        prefixed = compile_prompt("a street", BOORU, loras=loras)["positive"]
        suffixed = compile_prompt("a street", {**BOORU, "trigger_placement": "suffix"}, loras=loras)["positive"]

        self.assertLess(prefixed.index("neon glow"), prefixed.index("a street"))
        self.assertGreater(suffixed.index("neon glow"), suffixed.index("a street"))

    def test_a_target_length_truncates_on_a_tag_boundary(self):
        dialect = {**BOORU, "target_length": 40}
        compiled = compile_prompt("a very long description of a harbour at dawn with boats", dialect)

        self.assertLessEqual(len(compiled["positive"]), 40)
        self.assertTrue(compiled["truncated"])
        # A tag cut in half reads as a different concept to the model.
        self.assertFalse(compiled["positive"].endswith(","))

    def test_duplicate_trigger_words_are_stated_once(self):
        compiled = compile_prompt(
            "a street",
            BOORU,
            loras=[{"trigger_words": ["neon"]}, {"trigger_words": ["neon"]}],
        )
        self.assertEqual(compiled["trigger_words"], ["neon"])


class DialectExecutionTests(unittest.TestCase):
    def _graph(self) -> dict:
        return {
            "41": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved", "clip": ["12", 1]}},
            "42": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved negative", "clip": ["12", 1]}},
        }

    def test_the_client_sends_the_compiled_text_without_restyling_it(self):
        captured = {}
        settings = {
            "additional_parameters": json.dumps(self._graph()),
            "prompt_bindings": [{"node_id": "41", "input_name": "text"}],
            "negative_prompt_bindings": [{"node_id": "42", "input_name": "text"}],
            "compiled_prompt": "score_9, a lighthouse",
            "compiled_negative": "score_4",
        }
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=comfy_transport(captured)):
            comfyui_image("ignored", "512x512", "auto", False, "http://c.invalid:8188", settings, CancellationToken())

        self.assertEqual(captured["workflow"]["41"]["inputs"]["text"], "score_9, a lighthouse")
        self.assertEqual(captured["workflow"]["42"]["inputs"]["text"], "score_4")
        # `quality` and `allow_nsfw` must not re-add boilerplate on top.
        self.assertNotIn(LEGACY_QUALITY_PREFIX, captured["workflow"]["41"]["inputs"]["text"])


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class DialectJournalTests(unittest.TestCase):
    def test_the_compiled_prompt_and_dialect_are_recorded_in_the_journal(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a harbour at dawn"})
            media_id = running.wait_job(started.json()["job_id"])["result"]["mediaId"]

            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            stage = next(item for item in journal["stages"] if item["stage"] == "prompt_compiled")
            self.assertIn("a harbour at dawn", stage["detail"]["positive"])
            self.assertEqual(stage["detail"]["style"], "natural_language")
            self.assertIn("negative", stage["detail"])


class DialectCatalogTests(unittest.TestCase):
    def test_a_model_stores_a_validated_dialect_and_refuses_an_invalid_one(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            payload = {
                "resource_type": "model",
                "kind": "image",
                "name": "Illustrious",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "illustrious.safetensors",
                "operations": ["generate"],
                "default_settings": {"prompt_dialect": BOORU},
            }
            created = running.client.post("/api/v1/media-catalog/resources", json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["default_settings"]["prompt_dialect"]["style"], "booru")

            payload["external_id"] = "other.safetensors"
            payload["default_settings"] = {"prompt_dialect": {"style": "shakespearean"}}
            refused = running.client.post("/api/v1/media-catalog/resources", json=payload)
            self.assertEqual(refused.status_code, 400, refused.text)


if __name__ == "__main__":
    unittest.main()
