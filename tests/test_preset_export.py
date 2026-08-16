"""Exporting a preset as a file that means something on another machine.

A preset here is written in this installation's resource IDs. Export rewrites it
in names a person would recognise, carries nothing measured on this machine, and
names what could not travel rather than dropping it. See ADR 0030.
"""

from pathlib import Path
import json
import tempfile
import unittest

from app.preset_bundle import normalize_bundle
from app.preset_export import export_bundle, export_entry, preview, withheld
from tests.support import TestApp


class Row:
    """The parts of a preset row export reads."""

    def __init__(self, **values):
        self.name = values.get("name", "Portrait")
        self.kind = values.get("kind", "image")
        self.priority = values.get("priority", 50)
        self.routing_card = values.get("routing_card", "For portraits")
        self.notes = values.get("notes", "")
        self.operations_json = json.dumps(values.get("operations", ["generate"]))
        self.domains_json = json.dumps(values.get("domains", ["portrait"]))
        self.content_tags_json = json.dumps(values.get("content_tags", []))
        self.features_json = json.dumps(values.get("features", []))


class Resource:
    def __init__(self, resource_id: str, external_id: str):
        self.id = resource_id
        self.external_id = external_id


DEFINITION = {
    "base_model_resource_id": "r-model",
    "fixed_loras": [{"resource_id": "r-lora", "weight": 0.8}],
    "sampler": {"steps": 28, "cfg_scale": 5.5, "sampler_name": "dpmpp_2m"},
    "dimensions": ["832x1216"],
    "prompt_dialect": {"style": "booru"},
}
RESOURCES = {
    "r-model": Resource("r-model", "realistic-vision-v6.safetensors"),
    "r-lora": Resource("r-lora", "film-grain.safetensors"),
    "r-workflow": Resource("r-workflow", "portrait-graph.json"),
}


class ExportRecordTests(unittest.TestCase):
    def test_resources_leave_as_names_not_as_local_identifiers(self):
        entry = export_entry(Row(), DEFINITION, RESOURCES)

        self.assertEqual(entry["base_model_external_id"], "realistic-vision-v6.safetensors")
        self.assertEqual(entry["lora_external_ids"], ["film-grain.safetensors"])
        serialized = json.dumps(entry)
        for local in ("r-model", "r-lora"):
            self.assertNotIn(local, serialized)

    def test_a_workflow_is_named_as_a_requirement_rather_than_dropped(self):
        entry = export_entry(Row(), {**DEFINITION, "workflow_resource_id": "r-workflow"}, RESOURCES)

        # A graph carries this installation's node numbering, so it cannot
        # travel. Saying so beats arriving quietly different.
        self.assertTrue(any("portrait-graph.json" in item for item in entry["requirements"]))
        self.assertTrue(any("does not travel" in item for item in entry["requirements"]))

    def test_a_multi_pass_preset_names_every_pass_it_needs(self):
        definition = {
            **DEFINITION,
            "stages": [{"workflow_resource_id": "r-workflow"}, {"workflow_resource_id": "r-workflow"}],
        }

        entry = export_entry(Row(), definition, RESOURCES)

        self.assertEqual(sum("pass" in item for item in entry["requirements"]), 2)

    def test_an_identity_mechanism_is_named_as_a_requirement(self):
        entry = export_entry(Row(), {**DEFINITION, "identity_mechanisms": ["reference_adapter"]}, RESOURCES)

        self.assertTrue(any("reference_adapter" in item for item in entry["requirements"]))

    def test_an_asset_that_cannot_be_named_is_reported_not_silently_lost(self):
        entry = export_entry(Row(), {**DEFINITION, "base_model_resource_id": "gone"}, RESOURCES)

        self.assertEqual(entry["base_model_external_id"], "")
        self.assertTrue(any("could not name" in item for item in entry["requirements"]))

    def test_nothing_measured_on_this_machine_leaves(self):
        entry = export_entry(Row(), DEFINITION, RESOURCES)

        serialized = json.dumps(entry)
        for machine_specific in ("vram", "estimated", "http://", "https://", "C:\\\\", "/data/"):
            self.assertNotIn(machine_specific, serialized.casefold().replace("_", ""))

    def test_what_is_withheld_is_stated_rather_than_assumed(self):
        stated = " ".join(withheld()).casefold()

        self.assertIn("vram", stated)
        self.assertIn("path", stated)

    def test_the_preview_shows_the_fields_that_will_leave(self):
        rows = {row["label"]: row["value"] for row in preview(export_entry(Row(), DEFINITION, RESOURCES))}

        self.assertEqual(rows["Model file"], "realistic-vision-v6.safetensors")
        self.assertEqual(rows["Prompt style"], "booru")
        self.assertIn("steps 28", rows["Sampler"])

    def test_an_export_is_a_bundle_the_importer_would_accept(self):
        bundle = export_bundle([export_entry(Row(), DEFINITION, RESOURCES)])

        # Round-tripped through the same validation an imported file faces, so
        # an unusable export fails here and not on somebody else's machine.
        self.assertEqual(normalize_bundle(bundle)["presets"][0]["name"], "Portrait")


class ExportEndpointTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return running.client.get("/api/v1/media-catalog/presets").json()["items"]

    def test_a_preset_exports_with_its_preview_and_filename(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            presets = self._ready(running)
            self.assertTrue(presets, "no presets were bootstrapped")

            response = running.client.get(f"/api/v1/media-catalog/presets/{presets[0]['id']}/export")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["filename"].startswith("preset-"))
            self.assertTrue(payload["filename"].endswith(".json"))
            self.assertEqual(payload["bundle"]["version"], 1)
            self.assertTrue(payload["preview"])
            self.assertTrue(payload["withheld"])

    def test_exporting_something_that_does_not_exist_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            self.assertEqual(
                running.client.get("/api/v1/media-catalog/presets/nothing/export").status_code,
                404,
            )

    def test_an_export_is_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            presets = self._ready(running)
            running.client.delete("/api/v1/session")
            running.create_and_login("other")

            self.assertEqual(
                running.client.get(f"/api/v1/media-catalog/presets/{presets[0]['id']}/export").status_code,
                404,
            )


if __name__ == "__main__":
    unittest.main()
