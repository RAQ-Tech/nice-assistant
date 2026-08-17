"""Declared request bindings for operator ComfyUI workflows.

Before this, the executor wrote the prompt into fixed node IDs of a built-in
graph and merged the operator's graph over the top. An imported workflow
therefore rendered whatever text was saved inside it while still returning a
picture, so the failure was invisible. See ADR 0030.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.media_clients import comfyui_image
from app.provider_service import _inspect_comfyui_object_info
from app.provider_contracts import CancellationToken
from tests.support import TestApp


OBJECT_INFO = {
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}},
        "output": ["CONDITIONING"],
        "display_name": "CLIP Text Encode (Prompt)",
    },
    "KSampler": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "model": ["MODEL"],
            }
        },
        "output": ["LATENT"],
    },
    "EmptyLatentImage": {
        "input": {"required": {"width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}]}},
        "output": ["LATENT"],
    },
}


class Response:
    def __init__(self, payload=None, content=None):
        self.payload = payload
        self.content = content
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        return None

    def read(self, *_size):
        return self.content if self.content is not None else json.dumps(self.payload).encode()


def comfy_transport(captured):
    def fake_urlopen(request, timeout=0):
        if request.full_url.endswith("/prompt"):
            captured["workflow"] = json.loads(request.data.decode())["prompt"]
            return Response({"prompt_id": "prompt-1"})
        if "/history/" in request.full_url:
            return Response({"prompt-1": {"outputs": {"9": {"images": [{"filename": "out.png"}]}}}})
        if "/view?" in request.full_url:
            return Response(content=b"bound-image")
        raise AssertionError(request.full_url)

    return fake_urlopen


class WorkflowRequestBindingTests(unittest.TestCase):
    def _operator_graph(self) -> dict:
        # Deliberately arbitrary node IDs: nothing here matches the built-in
        # graph's 3/4/5/6/7/8/9, which is exactly the case that used to fail.
        return {
            "41": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved positive text", "clip": ["12", 1]}},
            "42": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved negative text", "clip": ["12", 1]}},
            "43": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
            "44": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "model": ["12", 0]}},
        }

    def test_a_graph_with_arbitrary_node_ids_receives_the_request(self):
        captured = {}
        settings = {
            "additional_parameters": json.dumps(self._operator_graph()),
            "prompt_bindings": [{"node_id": "41", "input_name": "text"}],
            "negative_prompt_bindings": [{"node_id": "42", "input_name": "text"}],
            "seed_bindings": [{"node_id": "44", "input_name": "seed"}],
            "width_bindings": [{"node_id": "43", "input_name": "width"}],
            "height_bindings": [{"node_id": "43", "input_name": "height"}],
        }
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=comfy_transport(captured)):
            content = comfyui_image(
                "a lighthouse in a storm",
                "768x1024",
                "none",
                True,
                "http://comfy-host.invalid:8188",
                settings,
                CancellationToken(),
            )

        self.assertEqual(content, b"bound-image")
        workflow = captured["workflow"]
        self.assertIn("a lighthouse in a storm", workflow["41"]["inputs"]["text"])
        self.assertNotIn("saved positive text", workflow["41"]["inputs"]["text"])
        self.assertNotEqual(workflow["42"]["inputs"]["text"], "saved negative text")
        self.assertEqual(workflow["43"]["inputs"]["width"], 768)
        self.assertEqual(workflow["43"]["inputs"]["height"], 1024)
        self.assertNotEqual(workflow["44"]["inputs"]["seed"], 1)

    def test_the_operator_graph_runs_whole_rather_than_merged_over_a_built_in_one(self):
        captured = {}
        settings = {
            "additional_parameters": json.dumps(self._operator_graph()),
            "prompt_bindings": [{"node_id": "41", "input_name": "text"}],
        }
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=comfy_transport(captured)):
            comfyui_image(
                "a kite", "512x512", "none", True, "http://comfy-host.invalid:8188", settings, CancellationToken()
            )

        # None of the built-in graph's nodes may leak into an operator workflow.
        self.assertEqual(sorted(captured["workflow"]), ["41", "42", "43", "44"])

    def test_a_binding_that_does_not_exist_fails_loudly(self):
        settings = {
            "additional_parameters": json.dumps(self._operator_graph()),
            "prompt_bindings": [{"node_id": "999", "input_name": "text"}],
        }
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=comfy_transport({})):
            with self.assertRaises(ValueError) as raised:
                comfyui_image("a kite", "512x512", "none", True, "http://c.invalid:8188", settings, CancellationToken())
        self.assertIn("prompt binding", str(raised.exception))

    def test_a_workflow_without_bindings_keeps_its_existing_behavior(self):
        captured = {}
        settings = {"additional_parameters": json.dumps({"55": {"class_type": "Note", "inputs": {"text": "hi"}}})}
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=comfy_transport(captured)):
            comfyui_image("a kite", "512x512", "none", True, "http://c.invalid:8188", settings, CancellationToken())

        # Legacy merge: the built-in graph is still there, so a workflow saved
        # before bindings existed produces exactly what it produced yesterday.
        self.assertIn("3", captured["workflow"])
        self.assertIn("a kite", captured["workflow"]["6"]["inputs"]["text"])


class WorkflowInspectionTests(unittest.TestCase):
    def test_inspection_names_the_text_seed_and_dimension_inputs_it_found(self):
        nodes = {
            "41": {"class_type": "CLIPTextEncode", "inputs": {"text": "a saved positive prompt", "clip": ["12", 1]}},
            "43": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
            "44": {"class_type": "KSampler", "inputs": {"seed": 7, "steps": 20, "model": ["12", 0]}},
        }
        result = _inspect_comfyui_object_info(nodes, OBJECT_INFO)
        candidates = result["request_input_candidates"]

        self.assertEqual([item["node_id"] for item in candidates["prompt"]], ["41"])
        self.assertEqual(candidates["prompt"][0]["current_value"], "a saved positive prompt")
        self.assertEqual([item["node_id"] for item in candidates["seed"]], ["44"])
        self.assertEqual([item["node_id"] for item in candidates["width"]], ["43"])
        self.assertEqual([item["node_id"] for item in candidates["height"]], ["43"])

    def test_an_input_driven_by_another_node_is_not_offered(self):
        nodes = {"41": {"class_type": "CLIPTextEncode", "inputs": {"text": ["12", 0], "clip": ["12", 1]}}}
        result = _inspect_comfyui_object_info(nodes, OBJECT_INFO)
        # Overwriting a linked input would silently break the operator's wiring.
        self.assertEqual(result["request_input_candidates"]["prompt"], [])

    def test_a_graph_with_no_text_input_is_reported_as_unable_to_receive_a_prompt(self):
        nodes = {"43": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}}}
        result = _inspect_comfyui_object_info(nodes, OBJECT_INFO)
        self.assertTrue(any("receive the request prompt" in warning for warning in result["warnings"]))


class WorkflowInspectionOverHttpTests(unittest.TestCase):
    """The service is not the contract; the response model is.

    Everything else here calls `_inspect_comfyui_object_info` directly, and the
    browser test mocks the client, so a field the response model did not name
    was computed, returned, and then thrown away by FastAPI. The browser gates
    its Save button on that field, which made guided identity setup impossible
    to complete on a real deployment while every test passed.
    """

    def _object_info(self, request, timeout=0):
        if request.full_url.endswith("/object_info"):
            return Response(OBJECT_INFO)
        raise AssertionError(request.full_url)

    def test_the_prompt_candidates_survive_the_response_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            with mock.patch("app.providers.urllib.request.urlopen", side_effect=self._object_info):
                inspected = running.client.post(
                    "/api/v1/media-catalog/identity-workflows/inspect",
                    json={
                        "workflow_patch": {
                            "41": {
                                "class_type": "CLIPTextEncode",
                                "inputs": {"text": "a saved prompt", "clip": ["12", 1]},
                            },
                            "43": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
                            "44": {"class_type": "KSampler", "inputs": {"seed": 7, "steps": 20, "model": ["12", 0]}},
                        }
                    },
                )

            self.assertEqual(inspected.status_code, 200, inspected.text)
            candidates = inspected.json()["request_input_candidates"]
            self.assertEqual([item["node_id"] for item in candidates["prompt"]], ["41"])
            # The preview is how an operator tells the positive prompt input
            # from the negative one, so it has to survive too.
            self.assertEqual(candidates["prompt"][0]["current_value"], "a saved prompt")
            self.assertEqual([item["node_id"] for item in candidates["seed"]], ["44"])
            self.assertEqual([item["node_id"] for item in candidates["width"]], ["43"])
            self.assertEqual([item["node_id"] for item in candidates["height"]], ["43"])


class WorkflowSaveRuleTests(unittest.TestCase):
    def _model(self, running):
        return running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "image",
                "name": "Base",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "base.safetensors",
                "operations": ["generate"],
            },
        ).json()

    def _workflow(self, model_id: str, *, enabled: bool, bindings: bool) -> dict:
        settings = {
            "workflow_patch": {"41": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved text"}}},
        }
        if bindings:
            settings["prompt_bindings"] = [{"node_id": "41", "input_name": "text"}]
        return {
            "resource_type": "workflow",
            "kind": "image",
            "name": "Operator workflow",
            "provider_key": "local-image",
            "backend": "comfyui",
            "external_id": "operator-workflow",
            "operations": ["generate"],
            "enabled": enabled,
            "default_settings": settings,
            "compatible_model_ids": [model_id],
        }

    def test_an_enabled_workflow_that_cannot_receive_the_prompt_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            refused = running.client.post(
                "/api/v1/media-catalog/resources",
                json=self._workflow(model["id"], enabled=True, bindings=False),
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("prompt binding", refused.text)

    def test_a_bound_workflow_saves_and_is_not_flagged_for_review(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            created = running.client.post(
                "/api/v1/media-catalog/resources",
                json=self._workflow(model["id"], enabled=True, bindings=True),
            )
            self.assertEqual(created.status_code, 201, created.text)
            self.assertFalse(created.json()["needs_binding_review"])
            self.assertEqual(
                created.json()["default_settings"]["prompt_bindings"],
                [{"node_id": "41", "input_name": "text"}],
            )

    def test_a_binding_must_name_an_input_that_exists_in_the_graph(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            payload = self._workflow(model["id"], enabled=True, bindings=True)
            payload["default_settings"]["prompt_bindings"] = [{"node_id": "41", "input_name": "not_an_input"}]
            refused = running.client.post("/api/v1/media-catalog/resources", json=payload)
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("prompt binding", refused.text)


if __name__ == "__main__":
    unittest.main()
