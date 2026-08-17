"""Presets declare which identity mechanisms they implement.

The persona Identity Spec says how resemblance is produced. A preset either has
the wiring to apply that mechanism or it does not, and nothing infers it: a
persona image must not run against a recipe that cannot honor its spec. See
ADR 0031.
"""

from pathlib import Path
import tempfile
import unittest

from app.media_preset import normalize_definition
from app.service_errors import RequestError
from tests.support import TestApp


class MechanismDeclarationTests(unittest.TestCase):
    def test_a_preset_may_declare_the_mechanisms_it_implements(self):
        definition = normalize_definition(
            {"base_model_resource_id": "m1", "identity_mechanisms": ["reference_adapter", "identity_pass"]}
        )
        self.assertEqual(definition["identity_mechanisms"], ["reference_adapter", "identity_pass"])

    def test_declaring_nothing_means_it_implements_nothing(self):
        definition = normalize_definition({"base_model_resource_id": "m1"})
        self.assertEqual(definition["identity_mechanisms"], [])

    def test_an_unknown_mechanism_is_refused_by_name(self):
        with self.assertRaises(RequestError) as raised:
            normalize_definition({"base_model_resource_id": "m1", "identity_mechanisms": ["face_magic"]})
        self.assertIn("reference_adapter", str(raised.exception))


class MechanismPlanningTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _persona(self, running, mechanism: str) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = running.client.post(
            "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}
        ).json()
        running.client.put(
            f"/api/v1/personas/{persona['id']}/visual-identity",
            json={"conditioning_mechanism": mechanism, "conditioning_fallback": "require_conditioning"},
        )
        return persona

    def _preview(self, running, persona_id: str) -> dict:
        return running.client.post(
            "/api/v1/media-catalog/plan-previews",
            json={
                "kind": "image",
                "operation": "generate",
                "domains": [],
                "content_tags": [],
                "required_features": ["identity_control"],
                "persona_id": persona_id,
            },
        )

    def _identity_workflow(self, running, model_id: str) -> dict:
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "workflow",
                "kind": "image",
                "name": "Reference conditioned",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "identity-workflow",
                "operations": ["generate"],
                "features": ["identity_control"],
                "default_settings": {
                    "workflow_patch": {
                        "41": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved"}},
                        "42": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
                    },
                    "prompt_bindings": [{"node_id": "41", "input_name": "text"}],
                    "identity_image_bindings": [{"node_id": "42", "input_name": "image"}],
                },
                "compatible_model_ids": [model_id],
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def test_a_backfilled_preset_claims_no_mechanism_the_catalog_cannot_supply(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            self.assertTrue(presets)
            # Nothing in the catalog conditions on a reference, so claiming the
            # mechanism here would make the filter that exists to reject an
            # incapable preset reject nothing at all.
            self.assertEqual(presets[0]["definition"]["identity_mechanisms"], [])

    def test_a_backfilled_preset_claims_the_mechanism_the_catalog_supplies(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            catalog = running.client.get("/api/v1/media-catalog").json()
            model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
            self._identity_workflow(running, model["id"])

            running.client.delete(
                f"/api/v1/media-catalog/presets/{running.client.get('/api/v1/media-catalog/presets').json()['items'][0]['id']}"
            )
            presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]

            self.assertTrue(presets)
            self.assertEqual(presets[0]["definition"]["identity_mechanisms"], ["reference_adapter"])

    def test_a_preset_reaches_a_mechanism_its_attached_workflow_proves(self):
        from app.media_planner import _evaluate_preset

        class Row:
            def __init__(self, **values):
                self.__dict__.update(values)

        base = Row(
            id="m1",
            resource_type="model",
            kind="image",
            provider_key="local-image",
            backend="comfyui",
            enabled=1,
            priority=50,
            estimated_vram_mb=1000,
            name="Base",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json="[]",
            default_settings_json="{}",
        )
        graph = Row(
            id="w1",
            resource_type="workflow",
            kind="image",
            provider_key="local-image",
            backend="comfyui",
            enabled=1,
            priority=50,
            estimated_vram_mb=0,
            name="Reference conditioned",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json='["identity_control"]',
            default_settings_json='{"identity_image_bindings": [{"node_id": "42", "input_name": "image"}]}',
        )
        preset = Row(
            id="p1",
            name="Open workflow slot",
            priority=50,
            routing_card="",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json="[]",
            definition_json="{}",
        )

        class Providers:
            media_providers = {"local-image": object()}

        class Setting:
            vram_budget_mb = 0
            max_loras = 0

        evaluated = _evaluate_preset(
            preset,
            # Written before the identity workflow existed, so it declares
            # nothing. The graph the plan selects proves what it can do, which
            # is what stops a stored guess being the only thing that decides.
            {"base_model_resource_id": "m1", "identity_mechanisms": [], "workflow_slot": {"enabled": True}},
            resources={"m1": base, "w1": graph},
            compatibility={"w1": {"m1"}},
            setting=Setting(),
            providers=Providers(),
            ready_backends=None,
            operation="generate",
            desired_domains=set(),
            required_content=set(),
            required_features={"identity_control"},
            required_mechanism="reference_adapter",
        )

        self.assertEqual(evaluated["reasons"], [])
        self.assertEqual(evaluated["workflow"].id, "w1")

    def test_a_workflow_without_reference_bindings_proves_nothing(self):
        from app.media_planner import workflow_mechanisms

        class Row:
            def __init__(self, **values):
                self.__dict__.update(values)

        # Declaring the feature is not the same as naming where the reference
        # goes, and only the binding makes the reference reach the graph.
        self.assertEqual(
            workflow_mechanisms(Row(features_json='["identity_control"]', default_settings_json="{}")),
            set(),
        )
        self.assertEqual(workflow_mechanisms(None), set())

    def test_a_preset_that_cannot_honor_the_spec_is_rejected_by_name(self):
        from app.media_planner import _evaluate_preset

        class Row:
            def __init__(self, **values):
                self.__dict__.update(values)

        base = Row(
            id="m1",
            resource_type="model",
            kind="image",
            provider_key="local-image",
            backend="comfyui",
            enabled=1,
            priority=50,
            estimated_vram_mb=1000,
            name="Base",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json='["identity_control"]',
            default_settings_json="{}",
        )
        preset = Row(
            id="p1",
            name="No identity wiring",
            priority=50,
            routing_card="",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json='["identity_control"]',
            definition_json='{"base_model_resource_id": "m1", "identity_mechanisms": []}',
        )

        class Providers:
            media_providers = {"local-image": object()}

        class Setting:
            vram_budget_mb = 0
            max_loras = 0

        evaluated = _evaluate_preset(
            preset,
            {"base_model_resource_id": "m1", "identity_mechanisms": []},
            resources={"m1": base},
            compatibility={},
            setting=Setting(),
            providers=Providers(),
            ready_backends=None,
            operation="generate",
            desired_domains=set(),
            required_content=set(),
            required_features={"identity_control"},
            required_mechanism="reference_adapter",
        )

        self.assertTrue(
            any("reference_adapter" in reason for reason in evaluated["reasons"]),
            evaluated["reasons"],
        )

    def test_a_preset_declaring_the_required_mechanism_is_accepted(self):
        from app.media_planner import _evaluate_preset

        class Row:
            def __init__(self, **values):
                self.__dict__.update(values)

        base = Row(
            id="m1",
            resource_type="model",
            kind="image",
            provider_key="local-image",
            backend="comfyui",
            enabled=1,
            priority=50,
            estimated_vram_mb=1000,
            name="Base",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json='["identity_control"]',
            default_settings_json="{}",
        )
        preset = Row(
            id="p1",
            name="Reference conditioned",
            priority=50,
            routing_card="",
            operations_json='["generate"]',
            domains_json="[]",
            content_tags_json="[]",
            features_json='["identity_control"]',
            definition_json='{"base_model_resource_id": "m1"}',
        )

        class Providers:
            media_providers = {"local-image": object()}

        class Setting:
            vram_budget_mb = 0
            max_loras = 0

        evaluated = _evaluate_preset(
            preset,
            {"base_model_resource_id": "m1", "identity_mechanisms": ["reference_adapter"]},
            resources={"m1": base},
            compatibility={},
            setting=Setting(),
            providers=Providers(),
            ready_backends=None,
            operation="generate",
            desired_domains=set(),
            required_content=set(),
            required_features={"identity_control"},
            required_mechanism="reference_adapter",
        )

        self.assertEqual(evaluated["reasons"], [])


if __name__ == "__main__":
    unittest.main()
