"""Shipped ComfyUI graphs, with their bindings declared rather than discovered.

Guided setup used to ask a person to export a graph in API format and choose
which of its inputs receives the prompt and which receives the reference. These
tests pin the other direction: the graph ships, its bindings are written with
it, and inspection answers whether this installation can run it.

They also pin what a template must not do - claim it has been run here, or imply
it checked an asset that nothing can see. See ADR 0030 and ADR 0031.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.media_catalog_service import MediaCatalogService
from app.service_errors import RequestError
from app.workflow_template import (
    MODEL_ARCHITECTURES,
    available_templates,
    normalize_template,
    resolve_template,
    template_default_settings,
)
from tests.support import TestApp
from tests.test_workflow_bindings import Response


class ShippedTemplateTests(unittest.TestCase):
    def test_every_shipped_template_normalizes(self):
        templates = available_templates()

        self.assertTrue(templates)
        for template in templates:
            self.assertIn(template["mechanism"], ("reference_adapter", "identity_pass"))
            self.assertTrue(set(template["architectures"]) <= set(MODEL_ARCHITECTURES))
            self.assertTrue(template["summary"], template["id"])
            self.assertTrue(template["required_assets"], template["id"])

    def test_every_shipped_binding_targets_an_input_of_its_own_graph(self):
        for template in available_templates():
            settings = template_default_settings(template)
            # Validated by the same code that validates an operator's own
            # bindings, so a template cannot ship something a person could not
            # have saved by hand.
            normalized = MediaCatalogService._normalize_default_settings("workflow", "local-image", "comfyui", settings)
            self.assertTrue(normalized["prompt_bindings"], template["id"])
            self.assertTrue(normalized["identity_image_bindings"], template["id"])

    def test_a_reference_binding_targets_the_node_that_loads_the_image(self):
        for template in available_templates():
            graph = template["workflow"]
            for binding in template["bindings"]["identity_image_bindings"]:
                node = graph[binding["node_id"]]
                # The executor writes an uploaded filename, which only means
                # something on a loader. Writing it into the identity node's
                # own IMAGE input would replace a link with a string.
                self.assertEqual(node["class_type"], "LoadImage", template["id"])

    def test_a_template_that_needs_a_trigger_word_ships_a_prefix_containing_it(self):
        for template in available_templates():
            if not template["required_prompt_token"]:
                continue
            self.assertIn(template["required_prompt_token"], template["prompt_prefix"], template["id"])
            settings = template_default_settings(template)
            self.assertEqual(settings["required_prompt_token"], template["required_prompt_token"])

    def test_a_template_promising_a_word_it_cannot_supply_is_refused(self):
        base = json.loads(Path("assets/workflow-templates/photomaker-v2-sdxl.json").read_text(encoding="utf-8"))
        with self.assertRaises(RequestError) as raised:
            normalize_template({**base, "prompt_prefix": "a portrait of a person"})
        self.assertIn("photomaker", str(raised.exception))

    def test_a_template_that_cannot_receive_the_prompt_is_refused(self):
        base = json.loads(Path("assets/workflow-templates/instantid-sdxl.json").read_text(encoding="utf-8"))
        bindings = {key: value for key, value in base["bindings"].items() if key != "prompt_bindings"}
        with self.assertRaises(RequestError) as raised:
            normalize_template({**base, "bindings": bindings})
        self.assertIn("prompt", str(raised.exception))

    def test_resolving_an_unknown_template_is_a_not_found(self):
        from app.service_errors import NotFoundError

        with self.assertRaises(NotFoundError):
            resolve_template("no-such-template")


class ModelFixture:
    def _model(self, running, architecture: str = "") -> dict:
        settings = {"architecture": architecture} if architecture else {}
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "image",
                "name": "Photoreal base",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "photoreal.safetensors",
                "operations": ["generate"],
                "default_settings": settings,
            },
        )
        assert created.status_code == 201, created.text
        return created.json()


class TemplateOfferTests(ModelFixture, unittest.TestCase):
    def test_a_model_declares_the_family_it_belongs_to(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")

            self.assertEqual(model["default_settings"]["architecture"], "sdxl")

    def test_an_unknown_family_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            refused = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "image",
                    "name": "Mystery base",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "mystery.safetensors",
                    "operations": ["generate"],
                    "default_settings": {"architecture": "sdxl-turbo-maybe"},
                },
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("sdxl", refused.text)

    def test_templates_are_offered_against_a_declared_family(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "pony")
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()

            self.assertEqual(listed["model_architecture"], "pony")
            self.assertTrue(listed["templates"])
            # SDXL templates operate on the SDXL text encoder, which Pony
            # retrains. They are shown and marked, not hidden: the operator may
            # know something the declaration does not.
            self.assertTrue(all(not item["architecture_matches"] for item in listed["templates"]))

    def test_an_undeclared_family_is_a_prompt_to_record_one_rather_than_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()

            self.assertEqual(listed["model_architecture"], "")
            self.assertTrue(all(item["architecture_matches"] for item in listed["templates"]))


class TemplateInstallTests(ModelFixture, unittest.TestCase):
    def _object_info(self, request, timeout=0):
        if request.full_url.endswith("/object_info"):
            return Response({"CheckpointLoaderSimple": {"input": {"required": {}}, "output": ["MODEL"]}})
        raise AssertionError(request.full_url)

    def _install(self, running, model_id: str, template_id: str = "photomaker-v2-sdxl"):
        return running.client.post(
            f"/api/v1/media-catalog/workflow-templates/{template_id}/installations",
            json={"model_id": model_id},
        )

    def test_installing_writes_a_bound_workflow_paired_with_the_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            created = self._install(running, model["id"])

            self.assertEqual(created.status_code, 201, created.text)
            resource = created.json()
            self.assertEqual(resource["compatible_model_ids"], [model["id"]])
            self.assertEqual(resource["features"], ["identity_control"])
            self.assertFalse(resource["needs_binding_review"])
            settings = resource["default_settings"]
            self.assertTrue(settings["prompt_bindings"])
            self.assertTrue(settings["identity_image_bindings"])
            # The graph takes the paired model rather than the placeholder
            # checkpoint name written into the shipped file.
            self.assertTrue(settings["checkpoint_bindings"])
            self.assertIn("not been generation-tested here", resource["notes"])

    def test_the_installed_graph_records_where_it_came_from(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            resource = self._install(running, model["id"]).json()

            self.assertEqual(resource["source_template_id"], "photomaker-v2-sdxl")
            self.assertEqual(resource["source_template_version"], 1)
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()
            entry = next(item for item in listed["templates"] if item["id"] == "photomaker-v2-sdxl")
            self.assertEqual(entry["installed_resource_id"], resource["id"])
            # Same version, so there is nothing to offer.
            self.assertFalse(entry["update_available"])

    def test_a_template_is_refused_for_a_family_it_was_not_built_for(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "flux")
            refused = self._install(running, model["id"])

            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("flux", refused.text)

    def test_installing_twice_gives_two_graphs_rather_than_overwriting_one(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            first = self._install(running, model["id"]).json()
            second = self._install(running, model["id"])

            # An operator may have tuned the first one. Nothing here rewrites
            # a graph somebody is already using.
            self.assertEqual(second.status_code, 201, second.text)
            self.assertNotEqual(second.json()["id"], first["id"])

    def test_verification_asks_the_provider_rather_than_asserting_it_works(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            with mock.patch("app.providers.urllib.request.urlopen", side_effect=self._object_info):
                verified = running.client.post("/api/v1/media-catalog/workflow-templates/photomaker-v2-sdxl/verify")

            self.assertEqual(verified.status_code, 200, verified.text)
            body = verified.json()
            # This ComfyUI reports one of the template's node types, so the
            # rest are named as missing rather than the template being called
            # compatible.
            self.assertFalse(body["provider_compatible"])
            self.assertIn("PhotoMakerEncodeV2", body["missing_node_types"])

    def test_verifying_an_unknown_template_is_a_not_found(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            missing = running.client.post("/api/v1/media-catalog/workflow-templates/nope/verify")

            self.assertEqual(missing.status_code, 404, missing.text)


if __name__ == "__main__":
    unittest.main()
