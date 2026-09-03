"""Models come from ComfyUI's own list, not from typing filenames.

The catalog knew exactly one model while forty-five checkpoints sat installed
in ComfyUI, because the only way to add one was a pair of bare prompt dialogs
asking for a filename typed exactly right. Every picture therefore ran through
one model and one recipe, which the owner experienced as everything having the
same vibe.

These pin the discovery path: ComfyUI is asked for its installed checkpoints,
the answer is annotated with what the catalog already has, ticking names
creates enabled models by exact filename, and the ordinary lazy preset pass
turns each new model into a recipe the planner can offer.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.support import TestApp

OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {
            "required": {
                "ckpt_name": [["dreamshaper.safetensors", "juggernaut.safetensors", "anything-v5.safetensors"]],
            }
        },
        "output": ["MODEL", "CLIP", "VAE"],
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}},
        "output": ["CONDITIONING"],
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "seed": ["INT", {"default": 0}],
                "sampler_name": [["euler", "dpmpp_2m", "dpmpp_2m_sde"]],
                "scheduler": [["normal", "karras"]],
            }
        },
        "output": ["LATENT"],
    },
    "EmptyLatentImage": {
        "input": {"required": {"width": ["INT", {}], "height": ["INT", {}]}},
        "output": ["LATENT"],
    },
    "VAEDecode": {
        "input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}},
        "output": ["IMAGE"],
    },
    "SaveImage": {
        "input": {"required": {"images": ["IMAGE"]}},
        "output": [],
        "output_node": True,
    },
}


def _object_info_transport(request, timeout=0, **_kwargs):
    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_size):
            return json.dumps(OBJECT_INFO).encode()

    assert request.full_url.endswith("/object_info"), request.full_url
    return Response()


class DiscoveryTests(unittest.TestCase):
    def _running(self, tmp):
        running = TestApp(Path(tmp)).__enter__()
        running.create_and_login()
        return running

    def _discover(self, running):
        with mock.patch("app.providers.urllib.request.urlopen", side_effect=_object_info_transport):
            return running.client.post("/api/v1/media-catalog/comfyui-checkpoints", json={})

    def test_comfyui_names_the_models_and_the_catalog_marks_what_it_has(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                listing = self._discover(running)
            finally:
                running.__exit__(None, None, None)

        self.assertEqual(listing.status_code, 200, listing.text)
        body = listing.json()
        self.assertTrue(body["ok"])
        names = [entry["name"] for entry in body["checkpoints"]]
        self.assertEqual(names, sorted(names))
        self.assertIn("juggernaut.safetensors", names)
        # A fresh account has cataloged none of them.
        self.assertFalse(any(entry["cataloged"] for entry in body["checkpoints"]))
        # The sampler vocabulary rides along, so the model page can offer what
        # is installed instead of asking somebody to type algorithm names.
        self.assertIn("euler", body["samplers"])
        self.assertIn("karras", body["schedulers"])

    def test_ticking_names_creates_enabled_models_and_recipes_follow(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                added = running.client.post(
                    "/api/v1/media-catalog/models/from-checkpoints",
                    json={"names": ["dreamshaper.safetensors", "juggernaut.safetensors"]},
                )
                self.assertEqual(added.status_code, 200, added.text)
                self.assertEqual(len(added.json()["added"]), 2)

                catalog = running.client.get("/api/v1/media-catalog").json()
                models = [row for row in catalog["resources"] if row["resource_type"] == "model" and row["enabled"]]
                presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            finally:
                running.__exit__(None, None, None)

        # Exact filenames, because that is what the provider will be asked for.
        self.assertEqual(
            sorted(row["external_id"] for row in models),
            ["dreamshaper.safetensors", "juggernaut.safetensors"],
        )
        # One recipe per model appears through the ordinary lazy pass: adding
        # models is adding variety the planner can actually offer.
        preset_names = " ".join(item["name"] for item in presets)
        self.assertIn("dreamshaper", preset_names)
        self.assertIn("juggernaut", preset_names)

    def test_adding_again_skips_rather_than_duplicating(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                first = running.client.post(
                    "/api/v1/media-catalog/models/from-checkpoints",
                    json={"names": ["dreamshaper.safetensors"]},
                )
                second = running.client.post(
                    "/api/v1/media-catalog/models/from-checkpoints",
                    json={"names": ["dreamshaper.safetensors"]},
                )
                listing = self._discover(running)
            finally:
                running.__exit__(None, None, None)

        self.assertEqual(first.json()["added"], ["dreamshaper.safetensors"])
        self.assertEqual(second.json()["added"], [])
        self.assertEqual(second.json()["skipped"][0]["reason"], "already cataloged")
        marked = {entry["name"]: entry["cataloged"] for entry in listing.json()["checkpoints"]}
        self.assertTrue(marked["dreamshaper.safetensors"])
        self.assertFalse(marked["juggernaut.safetensors"])

    def test_added_models_respect_the_operator_nsfw_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                running.client.post(
                    "/api/v1/media-catalog/models/from-checkpoints",
                    json={"names": ["dreamshaper.safetensors"]},
                )
                settings = running.client.get("/api/v1/settings").json()
                preferences = dict(settings["preferences"])
                preferences["image_local_allow_nsfw"] = True
                running.client.put("/api/v1/settings", json={**settings, "preferences": preferences})
                running.client.post(
                    "/api/v1/media-catalog/models/from-checkpoints",
                    json={"names": ["juggernaut.safetensors"]},
                )
                rows = {
                    row["external_id"]: row["content_tags"]
                    for row in running.client.get("/api/v1/media-catalog").json()["resources"]
                    if row["resource_type"] == "model"
                }
            finally:
                running.__exit__(None, None, None)

        # Adding a model must not quietly widen what the deployment will make:
        # the tag set follows the operator's NSFW choice at the moment of
        # adding, exactly as the legacy import gated it.
        self.assertEqual(rows["dreamshaper.safetensors"], ["general"])
        self.assertEqual(
            sorted(rows["juggernaut.safetensors"]),
            ["adult", "explicit", "general", "nudity"],
        )

    def test_comfyui_being_down_is_an_answer_rather_than_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                import urllib.error

                with mock.patch(
                    "app.providers.urllib.request.urlopen",
                    side_effect=urllib.error.URLError("refused"),
                ):
                    listing = running.client.post("/api/v1/media-catalog/comfyui-checkpoints", json={})
            finally:
                running.__exit__(None, None, None)

        self.assertEqual(listing.status_code, 200, listing.text)
        body = listing.json()
        self.assertFalse(body["ok"])
        self.assertIn("not reachable", body["message"])
        self.assertEqual(body["checkpoints"], [])


class PrefillTests(unittest.TestCase):
    """Suggestions carry their provenance: file, filename, or nothing."""

    def _prefill(self, checkpoint, transport):
        with tempfile.TemporaryDirectory() as tmp:
            running = TestApp(Path(tmp)).__enter__()
            try:
                running.create_and_login()
                with mock.patch("app.providers.urllib.request.urlopen", side_effect=transport):
                    return running.client.post(
                        "/api/v1/media-catalog/model-prefill",
                        json={"checkpoint": checkpoint},
                    ).json()
            finally:
                running.__exit__(None, None, None)

    def test_metadata_inside_the_file_names_the_family(self):
        def transport(request, timeout=0, **_kwargs):
            class Response:
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, *_size):
                    return json.dumps({"modelspec.architecture": "stable-diffusion-xl-v1-base"}).encode()

            assert "/view_metadata/checkpoints" in request.full_url, request.full_url
            return Response()

        body = self._prefill("mystery.safetensors", transport)

        self.assertEqual(body["source"], "file")
        self.assertEqual(body["family"], "sdxl")
        # The full suggestion set is present; the exact numbers belong to the
        # table in app/model_prefill.py, not to this test.
        for key in ("width", "height", "steps", "cfg_scale", "prompt_style"):
            self.assertIn(key, body)

    def test_without_metadata_the_filename_is_a_labeled_guess(self):
        import urllib.error

        def transport(request, timeout=0, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

        body = self._prefill("juggernautXL_ragnarok.safetensors", transport)

        self.assertEqual(body["source"], "filename")
        self.assertEqual(body["family"], "sdxl")
        self.assertIn("guessed from the file name", body["message"])

    def test_chroma_is_its_own_family_not_flux(self):
        import urllib.error

        def transport(request, timeout=0, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

        # De-distilled Flux lineage runs at real CFG; inheriting Flux's 1.0
        # would be an actively wrong suggestion for the owner's own model.
        body = self._prefill("gonzalomoChroma_v30.safetensors", transport)

        self.assertEqual(body["family"], "chroma")
        from app.model_prefill import FAMILY_DEFAULTS

        self.assertEqual(body["cfg_scale"], FAMILY_DEFAULTS["chroma"]["cfg_scale"])

    def test_saying_nothing_is_an_honest_answer(self):
        import urllib.error

        def transport(request, timeout=0, **_kwargs):
            raise urllib.error.URLError("refused")

        body = self._prefill("mystery.safetensors", transport)

        self.assertEqual(body["source"], "none")
        self.assertIsNone(body["family"])

    def test_every_family_defines_the_same_suggestion_shape(self):
        from app.model_prefill import FAMILY_DEFAULTS

        # The rule, not the numbers: each family must be able to fill the same
        # fields, so the page never meets a half-defined suggestion.
        for family, defaults in FAMILY_DEFAULTS.items():
            self.assertEqual(
                sorted(defaults),
                ["cfg_scale", "height", "label", "prompt_style", "sampler_name", "scheduler", "steps", "width"],
                family,
            )


GENERAL_GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "dreamshaper.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved words", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0],
            "seed": 7,
            "sampler_name": "euler",
            "scheduler": "normal",
        },
    },
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0]}},
}


class GeneralInspectionTests(unittest.TestCase):
    """The identity bar and the general bar are different bars."""

    def _inspect(self, role):
        with tempfile.TemporaryDirectory() as tmp:
            running = TestApp(Path(tmp)).__enter__()
            try:
                running.create_and_login()
                with mock.patch("app.providers.urllib.request.urlopen", side_effect=_object_info_transport):
                    return running.client.post(
                        "/api/v1/media-catalog/identity-workflows/inspect",
                        json={"workflow_patch": GENERAL_GRAPH, "settings": {}, "role": role},
                    ).json()
            finally:
                running.__exit__(None, None, None)

    def test_an_ordinary_text_to_image_graph_passes_as_general(self):
        result = self._inspect("general")

        # No identity node anywhere, and that is fine: the graph has an output
        # and somewhere for the request prompt to land, which is all a general
        # workflow owes anybody.
        self.assertTrue(result["provider_compatible"], result)
        self.assertTrue(result["request_input_candidates"]["prompt"])

    def test_the_same_graph_still_fails_the_identity_bar(self):
        result = self._inspect("identity")

        # The stricter reading stays strict: a graph with no identity node must
        # not be accepted as an identity workflow just because a laxer mode
        # exists now.
        self.assertFalse(result["provider_compatible"])


if __name__ == "__main__":
    unittest.main()
