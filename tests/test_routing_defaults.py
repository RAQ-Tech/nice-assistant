"""What routing does when nothing has been set up yet.

The first real request on a deployment with forty-five checkpoints added from
ComfyUI's list and no routing cards went to an inpainting model, at four steps
and CFG 2 borrowed from the Image Generation page, with the words "send me a
picture of you at the beach" as the prompt. Each of those is a default, and
each default was wrong. These tests pin the better ones.
"""

from pathlib import Path
import tempfile
import unittest

from app.media import subject_from_request
from app.media_journal import redact
from app.model_prefill import checkpoint_role, sampler_defaults
from app.provider_contracts import MediaArtifact
from tests.support import TestApp


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class RequestWordsTests(unittest.TestCase):
    def test_the_asking_is_not_part_of_the_picture(self):
        self.assertEqual(subject_from_request("send me a picture of you at the beach"), "you at the beach")
        self.assertEqual(subject_from_request("Can you draw a cat on a fence?"), "cat on a fence")
        self.assertEqual(subject_from_request("I want to see the harbour at dawn."), "the harbour at dawn")
        self.assertEqual(subject_from_request("take a selfie of yourself"), "yourself")

    def test_you_means_the_persona(self):
        self.assertEqual(subject_from_request("send me a picture of you at the beach", "Nova"), "Nova at the beach")
        self.assertEqual(subject_from_request("Can you draw yourself as a pirate?", "Nova"), "Nova as a pirate")
        self.assertEqual(subject_from_request("show me your garden", "Nova"), "Nova's garden")
        self.assertEqual(subject_from_request("you are dancing in the rain", "Nova"), "Nova is dancing in the rain")

    def test_a_request_that_is_all_asking_keeps_its_words(self):
        # Better the words than nothing at all.
        self.assertEqual(subject_from_request("send me a picture"), "send me a picture")


class CheckpointRoleTests(unittest.TestCase):
    def test_the_filename_says_what_a_checkpoint_is_for(self):
        self.assertEqual(checkpoint_role("512-inpainting-ema.safetensors"), "inpainting")
        self.assertEqual(checkpoint_role("sd_xl_refiner_1.0.safetensors"), "refiner")
        self.assertEqual(checkpoint_role("juggernautXL_v9.safetensors"), "base")

    def test_a_recipe_without_numbers_runs_on_its_family_or_an_ordinary_start(self):
        sdxl, source = sampler_defaults("juggernautXL_v9.safetensors")
        self.assertEqual(sdxl["steps"], 30)
        self.assertIn("SDXL", source)
        unknown, source = sampler_defaults("mystery.safetensors")
        self.assertEqual((unknown["steps"], unknown["cfg_scale"], unknown["sampler_name"]), (25, 7.0, "dpmpp_2m"))
        self.assertIn("ordinary starting point", source)


class JournalRedactionTests(unittest.TestCase):
    def test_a_trigger_word_is_shown_and_a_token_is_not(self):
        shown = redact({"required_prompt_token": "photomaker", "api_token": "abc"})
        self.assertEqual(shown["required_prompt_token"], "photomaker")
        self.assertEqual(shown["api_token"], "[redacted]")


class RoutingDefaultsTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _add(self, running, names):
        added = running.client.post("/api/v1/media-catalog/models/from-checkpoints", json={"names": names})
        assert added.status_code == 200, added.text
        # The lazy pass that gives every model its recipe.
        running.client.get("/api/v1/media-catalog/presets")
        return added.json()

    def _preview(self, running) -> dict:
        previewed = running.client.post(
            "/api/v1/media-catalog/plan-previews",
            json={"kind": "image", "operation": "generate", "domains": [], "content_tags": [], "required_features": []},
        )
        assert previewed.status_code == 200, previewed.text
        return previewed.json()

    def test_a_checkpoint_added_from_the_list_is_told_what_it_is_for(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._add(
                running, ["512-inpainting-ema.safetensors", "sd_xl_refiner_1.0.safetensors", "photoreal.safetensors"]
            )
            resources = running.client.get("/api/v1/media-catalog").json()["resources"]
            by_file = {item["external_id"]: item for item in resources if item["resource_type"] == "model"}

            inpainting = by_file["512-inpainting-ema.safetensors"]
            self.assertEqual(inpainting["operations"], ["inpaint"])
            self.assertNotIn("text_to_image", inpainting["features"])
            self.assertIn("inpainting model", inpainting["notes"])
            refiner = by_file["sd_xl_refiner_1.0.safetensors"]
            self.assertFalse(refiner["enabled"])
            self.assertIn("refiner", refiner["notes"])
            base = by_file["photoreal.safetensors"]
            self.assertTrue(base["enabled"])
            self.assertEqual(base["operations"], ["generate"])

    def test_the_default_model_breaks_a_tie_instead_of_the_alphabet(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            self._add(running, ["aardvark.safetensors", "photoreal.safetensors"])
            # Nothing prefers a recipe: no card, equal priority, no domain. The
            # alphabet used to decide; the model chosen by hand decides now.
            running.client.put("/api/v1/settings", json={"preferences": {"image_local_model": "photoreal.safetensors"}})
            preview = self._preview(running)

            self.assertEqual(preview["status"], "ready", preview)
            chosen = preview["explanation"]["preset"]
            self.assertEqual(chosen["source"], "default_model")
            self.assertIn("Image Generation page", chosen["reason"])
            self.assertEqual(
                next(item["name"] for item in preview["selected_resources"] if item["resource_type"] == "model"),
                "photoreal",
            )

    def test_an_inpainting_checkpoint_cataloged_earlier_is_still_not_offered_for_a_prompt(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            # Cataloged by hand as a generating model, the way every model was
            # before the list learned to tell them apart.
            created = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "image",
                    "name": "512 inpainting",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "512-inpainting-ema.safetensors",
                    "operations": ["generate"],
                    "features": ["text_to_image"],
                },
            )
            assert created.status_code == 201, created.text
            self._add(running, ["photoreal.safetensors"])
            preview = self._preview(running)

            self.assertEqual(preview["status"], "ready", preview)
            chosen = next(item["name"] for item in preview["selected_resources"] if item["resource_type"] == "model")
            self.assertNotEqual(chosen, "512 inpainting")
            rejected = {item["name"]: item["reasons"] for item in preview["explanation"]["rejected"]}
            self.assertTrue(any("inpainting" in reason for reason in rejected.get("512 inpainting", [])), rejected)


if __name__ == "__main__":
    unittest.main()
