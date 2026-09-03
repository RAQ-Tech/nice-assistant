"""Setting up many models in one sitting.

Forty-five checkpoints were added from ComfyUI's list and none was ever given
a family, numbers, a name or trigger words, because each meant a page of its
own. These pin the pass that does it for all of them: what it fills, where it
says each fill came from, what it refuses to guess, and how a browser can take
it a few models at a time.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.provider_contracts import MediaArtifact
from tests.support import TestApp


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class Response:
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_size):
        return json.dumps(self._payload).encode()


# CivitAI's answer for the Juggernaut search: the exact file, with its trigger
# word and the creator's showcase numbers. The mystery file gets a near match
# only, which nothing here may adopt.
JUGGERNAUT = {
    "items": [
        {
            "id": 1,
            "name": "Juggernaut XL",
            "modelVersions": [
                {
                    "id": 11,
                    "name": "v9",
                    "baseModel": "SDXL 1.0",
                    "trainedWords": ["photoreal"],
                    "files": [{"name": "juggernautXL_v9.safetensors"}],
                    "images": [
                        {"meta": {"steps": 32, "cfgScale": 5.5, "sampler": "DPM++ 2M Karras", "Size": "1024x1024"}},
                        {"meta": {"steps": 32, "cfgScale": 5.5, "sampler": "DPM++ 2M Karras", "Size": "1024x1024"}},
                    ],
                }
            ],
        }
    ]
}
MYSTERY = {
    "items": [
        {
            "id": 2,
            "name": "Mystery Mix",
            "modelVersions": [
                {
                    "id": 22,
                    "name": "v1",
                    "baseModel": "SD 1.5",
                    "trainedWords": [],
                    "files": [{"name": "other.safetensors"}],
                }
            ],
        }
    ]
}


def transport_for(seen: list):
    def transport(request, timeout=0, **_kwargs):
        url = request.full_url
        seen.append(url)
        if "/view_metadata/checkpoints" in url:
            if "juggernaut" in url:
                return Response({"modelspec.architecture": "stable-diffusion-xl-v1-base"})
            return Response({})
        if "civitai.com/api/v1/models" in url:
            return Response(JUGGERNAUT if "juggernaut" in url.lower() else MYSTERY)
        if "civitai.com/api/v1/images" in url:
            return Response({"items": []})
        raise AssertionError(url)

    return transport


class ModelSetupTests(unittest.TestCase):
    def _ready(self, running, names):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        added = running.client.post("/api/v1/media-catalog/models/from-checkpoints", json={"names": names})
        assert added.status_code == 200, added.text

    def _setup(self, running, seen, **body):
        with mock.patch("app.providers.urllib.request.urlopen", side_effect=transport_for(seen)):
            response = running.client.post("/api/v1/media-catalog/models/setup", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def _model_and_recipe(self, running, filename):
        resources = running.client.get("/api/v1/media-catalog").json()["resources"]
        model = next(item for item in resources if item["external_id"] == filename)
        presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
        recipe = next(item for item in presets if item["definition"]["base_model_resource_id"] == model["id"])
        return model, recipe

    def test_the_pass_fills_what_the_file_and_the_exact_match_say_and_names_the_source(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(
                running, ["juggernautXL_v9.safetensors", "mystery.safetensors", "512-inpainting-ema.safetensors"]
            )
            seen: list = []
            report = self._setup(running, seen, limit=5, lookup=True)

            # The inpainting checkpoint is not a base model, so it is not a candidate.
            self.assertEqual(report["total"], 2)
            self.assertEqual(report["remaining"], 0)
            by_file = {item["file"]: item for item in report["processed"]}

            juggernaut = by_file["juggernautXL_v9.safetensors"]
            self.assertEqual(juggernaut["lookup"], "exact")
            self.assertEqual(juggernaut["name"], "Juggernaut XL")
            self.assertIn("family: SDXL (read from the file)", juggernaut["filled"])
            self.assertIn("name (CivitAI)", juggernaut["filled"])
            self.assertIn("steps and CFG (the creator's showcase on CivitAI)", juggernaut["filled"])
            self.assertIn("trigger words (CivitAI)", juggernaut["filled"])
            model, recipe = self._model_and_recipe(running, "juggernautXL_v9.safetensors")
            self.assertEqual(model["default_settings"]["architecture"], "sdxl")
            self.assertEqual(model["default_settings"]["setup"]["family"], "read from the file")
            self.assertEqual(recipe["name"], "Juggernaut XL")
            self.assertEqual(recipe["definition"]["sampler"]["steps"], 32)
            self.assertEqual(recipe["definition"]["sampler"]["sampler_name"], "dpmpp_2m")
            self.assertEqual(recipe["definition"]["dimensions"], ["1024x1024"])
            self.assertTrue(recipe["definition"]["prompt_dialect"]["prefix"].startswith("photoreal"))

            # A near match is a person's call, and nothing about it is adopted.
            mystery = by_file["mystery.safetensors"]
            self.assertEqual(mystery["lookup"], "nearest")
            self.assertTrue(any("pick one on the model's page" in note for note in mystery["notes"]))
            self.assertEqual(mystery["filled"], [])
            model, recipe = self._model_and_recipe(running, "mystery.safetensors")
            self.assertEqual(model["name"], "mystery")
            self.assertNotIn("architecture", model["default_settings"])
            # No routing card was written for anybody; the report says who has none.
            self.assertEqual(report["without_routing_card"], ["Juggernaut XL", "mystery"])

    def test_a_second_pass_finds_nothing_left_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running, ["juggernautXL_v9.safetensors"])
            seen: list = []
            self._setup(running, seen, limit=5, lookup=True)
            again = self._setup(running, seen, limit=5, lookup=True)
            self.assertEqual(again["processed"], [])
            self.assertEqual(again["remaining"], 0)
            forced = self._setup(running, seen, limit=5, lookup=True, force=True)
            self.assertEqual(len(forced["processed"]), 1)

    def test_a_browser_takes_the_pass_a_few_models_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running, ["juggernautXL_v9.safetensors", "mystery.safetensors"])
            seen: list = []
            first = self._setup(running, seen, limit=1, lookup=False)
            self.assertEqual(len(first["processed"]), 1)
            self.assertEqual(first["remaining"], 1)
            second = self._setup(running, seen, limit=1, lookup=False)
            self.assertEqual(len(second["processed"]), 1)
            self.assertEqual(second["remaining"], 0)

    def test_without_consent_nothing_leaves_the_machine(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running, ["juggernautXL_v9.safetensors"])
            seen: list = []
            report = self._setup(running, seen, limit=5, lookup=False)

            self.assertFalse(any("civitai" in url for url in seen), seen)
            juggernaut = report["processed"][0]
            self.assertEqual(juggernaut["lookup"], "skipped")
            # The file still says what family it is, and the family still has numbers.
            self.assertIn("family: SDXL (read from the file)", juggernaut["filled"])
            self.assertIn("steps and CFG (SDXL family defaults)", juggernaut["filled"])
            self.assertEqual(juggernaut["name"], "juggernautXL v9")


if __name__ == "__main__":
    unittest.main()
