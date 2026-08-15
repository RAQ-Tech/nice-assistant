"""Starter presets and the bundle format.

A bundle names assets the way a person does - by the filename the provider
reports - because this installation's resource IDs mean nothing anywhere else.
That is what makes the same format usable for the built-in starters now and for
sharing later. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.preset_bundle import BUNDLE_VERSION, normalize_bundle, starter_bundle
from app.service_errors import RequestError
from tests.support import TestApp


class BundleFormatTests(unittest.TestCase):
    def _entry(self, **overrides) -> dict:
        entry = {
            "name": "Example",
            "base_model_external_id": "example.safetensors",
            "sampler": {"steps": 30, "cfg_scale": 6.0},
            "dimensions": ["1024x1024"],
        }
        entry.update(overrides)
        return entry

    def test_a_bundle_needs_a_supported_version_and_at_least_one_preset(self):
        with self.assertRaises(RequestError):
            normalize_bundle({"version": 99, "presets": [self._entry()]})
        with self.assertRaises(RequestError):
            normalize_bundle({"version": BUNDLE_VERSION, "presets": []})

    def test_every_preset_must_name_the_model_file_it_expects(self):
        with self.assertRaises(RequestError) as raised:
            normalize_bundle({"version": BUNDLE_VERSION, "presets": [self._entry(base_model_external_id="")]})
        self.assertIn("model file", str(raised.exception))

    def test_a_malformed_definition_is_refused_before_anything_is_installed(self):
        with self.assertRaises(RequestError):
            normalize_bundle({"version": BUNDLE_VERSION, "presets": [self._entry(dimensions=["enormous"])]})
        with self.assertRaises(RequestError):
            normalize_bundle(
                {"version": BUNDLE_VERSION, "presets": [self._entry(prompt_dialect={"style": "shakespearean"})]}
            )

    def test_unsupported_fields_are_refused_by_name(self):
        with self.assertRaises(RequestError) as raised:
            normalize_bundle({"version": BUNDLE_VERSION, "presets": [self._entry(checkpoint="x")]})
        self.assertIn("checkpoint", str(raised.exception))


class StarterBundleTests(unittest.TestCase):
    def test_the_shipped_starters_are_valid_and_cover_distinct_dialects(self):
        bundle = starter_bundle()
        styles = {item["prompt_dialect"].get("style") for item in bundle["presets"]}
        self.assertIn("booru", styles)
        self.assertIn("natural_language", styles)

    def test_a_starter_for_a_model_without_negatives_declares_that(self):
        bundle = starter_bundle()
        flux = next(item for item in bundle["presets"] if "Flux" in item["name"])
        self.assertFalse(flux["prompt_dialect"]["supports_negative"])
        self.assertEqual(flux["prompt_dialect"]["negative_prompt"], "")

    def test_every_starter_says_it_is_untested_here(self):
        for item in starter_bundle()["presets"]:
            self.assertIn("not tested on this deployment", item["notes"].casefold())


class StarterInstallTests(unittest.TestCase):
    def _model(self, running, external_id: str, name: str = "Base") -> dict:
        return running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "image",
                "name": name,
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": external_id,
                "operations": ["generate"],
                "estimated_vram_mb": 7000,
            },
        ).json()

    def test_a_starter_whose_model_is_absent_is_named_rather_than_installed(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            listed = running.client.get("/api/v1/media-catalog/starter-presets").json()
            sdxl = next(item for item in listed["presets"] if item["name"].startswith("SDXL"))

            self.assertFalse(sdxl["installable"])
            self.assertIn("sd_xl_base_1.0.safetensors", sdxl["missing_assets"])

            result = running.client.post("/api/v1/media-catalog/starter-presets/install").json()
            self.assertEqual(result["installed"], [])
            reason = next(item["reason"] for item in result["skipped"] if item["name"].startswith("SDXL"))
            # Named, so the operator knows what to install rather than meeting a
            # failure at generation time.
            self.assertIn("sd_xl_base_1.0.safetensors", reason)

    def test_a_starter_installs_once_its_model_is_cataloged(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._model(running, "flux1-dev.safetensors", name="Flux dev")

            listed = running.client.get("/api/v1/media-catalog/starter-presets").json()
            flux = next(item for item in listed["presets"] if item["name"].startswith("Flux"))
            self.assertTrue(flux["installable"])

            result = running.client.post("/api/v1/media-catalog/starter-presets/install").json()
            self.assertIn("Flux (starter)", [item["name"] for item in result["installed"]])

            presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            installed = next(item for item in presets if item["name"] == "Flux (starter)")
            self.assertFalse(installed["definition"]["prompt_dialect"]["supports_negative"])
            self.assertEqual(installed["definition"]["sampler"]["cfg_scale"], 1.0)
            self.assertIn("prompt adherence", installed["routing_card"])

    def test_installing_twice_never_overwrites_what_the_operator_curated(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._model(running, "flux1-dev.safetensors", name="Flux dev")
            running.client.post("/api/v1/media-catalog/starter-presets/install")

            preset = next(
                item
                for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]
                if item["name"] == "Flux (starter)"
            )
            edited = running.client.put(
                f"/api/v1/media-catalog/presets/{preset['id']}",
                json={
                    "name": preset["name"],
                    "kind": preset["kind"],
                    "enabled": preset["enabled"],
                    "priority": 99,
                    "routing_card": "My own note.",
                    "operations": preset["operations"],
                    "domains": preset["domains"],
                    "content_tags": preset["content_tags"],
                    "features": preset["features"],
                    "definition": preset["definition"],
                    "estimated_vram_mb": preset["estimated_vram_mb"],
                    "notes": preset["notes"],
                },
            )
            self.assertEqual(edited.status_code, 200, edited.text)

            second = running.client.post("/api/v1/media-catalog/starter-presets/install").json()
            self.assertEqual(second["installed"], [])
            self.assertIn(
                "already exists",
                next(item["reason"] for item in second["skipped"] if item["name"] == "Flux (starter)"),
            )
            kept = next(
                item
                for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]
                if item["name"] == "Flux (starter)"
            )
            self.assertEqual(kept["routing_card"], "My own note.")
            self.assertEqual(kept["priority"], 99)

    def test_starter_presets_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._model(running, "flux1-dev.safetensors", name="Flux dev")
            running.client.post("/api/v1/media-catalog/starter-presets/install")

            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            names = [item["name"] for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]]
            self.assertNotIn("Flux (starter)", names)


if __name__ == "__main__":
    unittest.main()
