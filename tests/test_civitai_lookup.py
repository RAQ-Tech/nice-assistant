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
        seen = {"urls": [], "agents": []}

        def transport(request, timeout=0, **_kwargs):
            class Response:
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, *_size):
                    if "/api/v1/images" in request.full_url:
                        return json.dumps({"items": []}).encode()
                    return json.dumps(PAYLOAD).encode()

            seen["urls"].append(request.full_url)
            seen["agents"].append(request.get_header("User-agent"))
            return Response()

        body = self._lookup(transport)

        self.assertIn("civitai.com/api/v1/models", seen["urls"][0])
        self.assertIn("juggernautXL%20ragnarok", seen["urls"][0])
        # Cloudflare 403s Python's default agent string; every request names
        # the project instead.
        self.assertTrue(all(agent and "nice-assistant" in agent for agent in seen["agents"]), seen["agents"])
        self.assertTrue(body["ok"])
        self.assertTrue(body["matches"][0]["file_match"])

    def test_hidden_meta_falls_back_to_family_typical_settings(self):
        from app.model_prefill import FAMILY_DEFAULTS

        # The modern common case: the listing carries no generation meta and
        # neither do the community images. The declared base model still names
        # a family, so the match carries typical settings labeled as such.
        stripped = json.loads(json.dumps(PAYLOAD))
        for model in stripped["items"]:
            for version in model["modelVersions"]:
                version["images"] = []

        def transport(request, timeout=0, **_kwargs):
            class Response:
                headers = {}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, *_size):
                    if "/api/v1/images" in request.full_url:
                        return json.dumps({"items": [{"meta": None}]}).encode()
                    return json.dumps(stripped).encode()

            return Response()

        body = self._lookup(transport)

        match = body["matches"][0]
        self.assertEqual(match["settings_source"], "family")
        self.assertEqual(match["steps"], FAMILY_DEFAULTS["sdxl"]["steps"])
        self.assertEqual(match["family_label"], FAMILY_DEFAULTS["sdxl"]["label"])

    def test_distilled_variants_get_no_family_numbers(self):
        from app.civitai_lookup import apply_family_defaults

        # A Lightning model wants a handful of steps, not the family's thirty;
        # a wrong suggestion is worse than none, so distilled variants suggest
        # nothing rather than misleading.
        match = {"base_model": "SDXL Lightning"}
        apply_family_defaults(match)

        self.assertNotIn("settings_source", match)
        self.assertNotIn("steps", match)

    def test_a_refusal_is_named_as_a_refusal(self):
        import urllib.error

        def transport(request, timeout=0, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

        body = self._lookup(transport)

        self.assertFalse(body["ok"])
        self.assertIn("refused", body["message"])

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
