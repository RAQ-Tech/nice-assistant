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

    def test_a_backfilled_preset_keeps_reaching_reference_conditioning(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            self.assertTrue(presets)
            # Today's behavior attaches a reference-conditioned workflow, so a
            # preset derived from an existing model must say so.
            self.assertEqual(
                presets[0]["definition"]["identity_mechanisms"],
                ["reference_adapter"],
            )

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
