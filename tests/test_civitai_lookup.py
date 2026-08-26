"""The CivitAI lookup: filename in, reviewable matches out, honestly labeled.

The app cannot hash a model file it cannot read, so the search runs on the
filename and a person picks the match. These pin the parsing rules - exact
file matches first, settings taken from the creator's showcase images or not
at all, sampler names translated to ComfyUI's vocabulary - and that the whole
thing degrades to a plain answer when civitai.com is unreachable.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.civitai_lookup import parse_matches, search_query, translate_sampler
from tests.support import TestApp

PAYLOAD = {
    "items": [
        {
            "id": 133005,
            "name": "Juggernaut XL",
            "modelVersions": [
                {
                    "id": 456194,
                    "name": "Ragnarok",
                    "baseModel": "SDXL 1.0",
                    "trainedWords": [],
                    "files": [{"name": "juggernautXL_ragnarok.safetensors"}],
                    "images": [
                        {"meta": {"steps": 35, "cfgScale": 4.5, "sampler": "DPM++ 2M Karras", "Size": "832x1216"}},
                        {"meta": {"steps": 35, "cfgScale": 4.5, "sampler": "DPM++ 2M Karras", "Size": "832x1216"}},
                        {"meta": {"steps": 30, "cfgScale": 6, "sampler": "Euler a", "Size": "1024x1024"}},
                        {"meta": None},
                    ],
                }
            ],
        },
        {
            "id": 1,
            "name": "Juggernaut Classic",
            "modelVersions": [
                {
                    "id": 2,
                    "name": "v1",
                    "baseModel": "SD 1.5",
                    "trainedWords": ["jugg style"],
                    "files": [{"name": "juggernaut_v1.safetensors"}],
                    "images": [],
                }
            ],
        },
    ]
}


class ParsingTests(unittest.TestCase):
    def test_the_exact_file_match_leads_and_is_marked(self):
        matches = parse_matches(PAYLOAD, "juggernautXL_ragnarok.safetensors")

        self.assertTrue(matches[0]["file_match"])
        self.assertEqual(matches[0]["model_name"], "Juggernaut XL")
        self.assertFalse(matches[1]["file_match"])

    def test_settings_come_from_the_showcase_majority_translated_for_comfyui(self):
        match = parse_matches(PAYLOAD, "juggernautXL_ragnarok.safetensors")[0]

        # Two of three images agree; the majority wins, and the A1111 sampler
        # name arrives in ComfyUI's vocabulary with its scheduler split out.
        self.assertEqual(match["steps"], 35)
        self.assertEqual(match["cfg_scale"], 4.5)
        self.assertEqual(match["sampler"], "dpmpp_2m")
        self.assertEqual(match["scheduler"], "karras")
        self.assertEqual((match["width"], match["height"]), (832, 1216))

    def test_a_version_without_showcase_metadata_suggests_nothing(self):
        match = parse_matches(PAYLOAD, "juggernaut_v1.safetensors")[0]

        self.assertEqual(match["model_name"], "Juggernaut Classic")
        self.assertNotIn("steps", match)
        self.assertEqual(match["trigger_words"], ["jugg style"])

    def test_an_unmapped_sampler_passes_through_untranslated(self):
        name, scheduler = translate_sampler("Restart")

        self.assertEqual(name, "Restart")
        self.assertIsNone(scheduler)

    def test_the_query_is_the_filename_as_words(self):
        self.assertEqual(search_query("juggernautXL_ragnarok-v2.safetensors"), "juggernautXL ragnarok v2")


class LookupEndpointTests(unittest.TestCase):
    def _lookup(self, transport):
        with tempfile.TemporaryDirectory() as tmp:
            running = TestApp(Path(tmp)).__enter__()
            try:
                running.create_and_login()
                with mock.patch("app.providers.urllib.request.urlopen", side_effect=transport):
                    return running.client.post(
                        "/api/v1/media-catalog/civitai-lookup",
                        json={"checkpoint": "juggernautXL_ragnarok.safetensors"},
                    ).json()
            finally:
                running.__exit__(None, None, None)

    def test_the_search_goes_to_civitai_and_comes_back_reviewable(self):
        seen = {}

        def transport(request, timeout=0, **_kwargs):
            class Response:
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, *_size):
                    return json.dumps(PAYLOAD).encode()

            seen["url"] = request.full_url
            return Response()

        body = self._lookup(transport)

        self.assertIn("civitai.com/api/v1/models", seen["url"])
        self.assertIn("juggernautXL%20ragnarok", seen["url"])
        self.assertTrue(body["ok"])
        self.assertTrue(body["matches"][0]["file_match"])

    def test_unreachable_is_an_answer_rather_than_an_error(self):
        import urllib.error

        def transport(request, timeout=0, **_kwargs):
            raise urllib.error.URLError("refused")

        body = self._lookup(transport)

        self.assertFalse(body["ok"])
        self.assertIn("could not be reached", body["message"])
        self.assertEqual(body["matches"], [])


if __name__ == "__main__":
    unittest.main()
