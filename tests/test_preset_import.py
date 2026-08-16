"""Importing a preset somebody else exported.

The file names assets by filename, so import has to match them against what is
installed here. It is all or nothing: a partly installed file leaves a catalog
nobody can reason about. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.preset_bundle import normalize_bundle
from tests.support import TestApp


def bundle(**overrides) -> dict:
    entry = {
        "name": "Someone else's portrait recipe",
        "routing_card": "For portraits",
        "notes": "Tuned over a weekend",
        "kind": "image",
        "priority": 60,
        "operations": ["generate"],
        "domains": ["portrait"],
        "content_tags": [],
        "features": [],
        "base_model_external_id": "a-model-that-is-not-installed.safetensors",
        "lora_external_ids": [],
        "sampler": {"steps": 28},
        "dimensions": ["832x1216"],
        "prompt_dialect": {"style": "booru"},
        "lora_slots": [],
        "workflow_slot": {"enabled": False},
        "requirements": [],
    }
    entry.update(overrides)
    return {"version": 1, "presets": [entry]}


class ImportRecordTests(unittest.TestCase):
    def test_a_foreign_vram_figure_is_dropped_rather_than_refused(self):
        # An older or foreign file may carry one. Refusing the whole file over
        # it would be unhelpful; trusting it would be wrong.
        normalized = normalize_bundle(bundle(estimated_vram_mb=99999))

        self.assertNotIn("estimated_vram_mb", normalized["presets"][0])


class ImportEndpointTests(unittest.TestCase):
    def _ready(self, running, username="owner"):
        running.create_and_login(username)
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return running.client.get("/api/v1/media-catalog").json()["resources"]

    def _installed_model(self, running) -> str:
        models = [
            item
            for item in self._ready(running)
            if item["resource_type"] == "model" and item["kind"] == "image" and item["enabled"]
        ]
        assert models, "no image model was bootstrapped"
        return models[0]["external_id"]

    def _preview(self, running, payload) -> dict:
        response = running.client.post("/api/v1/media-catalog/presets/import/preview", json={"bundle": payload})
        assert response.status_code == 200, response.text
        return response.json()

    def _import(self, running, payload):
        return running.client.post("/api/v1/media-catalog/presets/import", json={"bundle": payload})

    def test_a_recipe_whose_model_is_installed_here_imports(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)
            payload = bundle(base_model_external_id=model)

            preview = self._preview(running, payload)
            self.assertTrue(preview["installable"], preview)

            imported = self._import(running, payload)
            self.assertEqual(imported.status_code, 200, imported.text)
            names = [item["name"] for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]]
            self.assertIn("Someone else's portrait recipe", names)

    def test_a_recipe_naming_a_model_that_is_not_here_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            before = len(running.client.get("/api/v1/media-catalog/presets").json()["items"])

            preview = self._preview(running, bundle())
            refused = self._import(running, bundle())

            self.assertFalse(preview["installable"])
            self.assertTrue(any("not installed here" in item for item in preview["presets"][0]["blockers"]))
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertIn("a-model-that-is-not-installed.safetensors", refused.text)
            # Nothing changed.
            self.assertEqual(len(running.client.get("/api/v1/media-catalog/presets").json()["items"]), before)

    def test_a_file_is_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)
            good = bundle(base_model_external_id=model)["presets"][0]
            bad = bundle(name="Unsatisfiable")["presets"][0]
            payload = {"version": 1, "presets": [good, bad]}
            before = len(running.client.get("/api/v1/media-catalog/presets").json()["items"])

            refused = self._import(running, payload)

            self.assertEqual(refused.status_code, 409, refused.text)
            # The satisfiable one is not installed either: a half-imported file
            # leaves a catalog nobody can reason about.
            self.assertEqual(len(running.client.get("/api/v1/media-catalog/presets").json()["items"]), before)

    def test_a_workflow_slot_is_declared_as_running_someone_elses_graph(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)
            payload = bundle(base_model_external_id=model, workflow_slot={"enabled": True})

            warnings = " ".join(self._preview(running, payload)["warnings"]).casefold()

            self.assertIn("graph on this machine", warnings)
            self.assertIn("trust", warnings)

    def test_every_import_says_it_was_tested_somewhere_else(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)

            warnings = " ".join(self._preview(running, bundle(base_model_external_id=model))["warnings"])

            self.assertIn("somebody else's installation", warnings)

    def test_requirements_travel_into_the_notes_so_they_are_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)
            payload = bundle(
                base_model_external_id=model,
                requirements=["A ComfyUI workflow, which does not travel: portrait-graph.json"],
            )

            self._import(running, payload)

            preset = next(
                item
                for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]
                if item["name"] == "Someone else's portrait recipe"
            )
            self.assertIn("portrait-graph.json", preset["notes"])

    def test_a_name_that_already_exists_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._installed_model(running)
            payload = bundle(base_model_external_id=model)
            self._import(running, payload)

            again = self._import(running, payload)

            self.assertEqual(again.status_code, 409, again.text)
            self.assertIn("already exists", again.text)

    def test_a_malformed_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            response = running.client.post(
                "/api/v1/media-catalog/presets/import/preview",
                json={"bundle": {"version": 99, "presets": []}},
            )

            self.assertEqual(response.status_code, 400, response.text)

    def test_an_exported_preset_imports_into_another_account(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            source = running.client.get("/api/v1/media-catalog/presets").json()["items"][0]
            exported = running.client.get(f"/api/v1/media-catalog/presets/{source['id']}/export").json()
            running.client.delete("/api/v1/session")
            self._ready(running, "other")

            # The round trip is the point: what leaves one machine has to be
            # usable on another with the same model installed.
            preview = self._preview(running, exported["bundle"])
            self.assertTrue(preview["installable"], preview)
            self.assertEqual(self._import(running, exported["bundle"]).status_code, 200)


if __name__ == "__main__":
    unittest.main()
