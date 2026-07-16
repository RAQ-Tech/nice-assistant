import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app.providers import provider_get_json
from tests.support import TestApp


def identity_workflow():
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": "persona-reference.png"}},
        "11": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
        "12": {
            "class_type": "IPAdapterAdvanced",
            "inputs": {"image": ["10", 0], "model": ["11", 0]},
        },
        "13": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "14": {"class_type": "CLIPTextEncode", "inputs": {"text": "portrait"}},
        "15": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["12", 0],
                "positive": ["14", 0],
                "negative": ["14", 0],
                "latent_image": ["13", 0],
                "seed": 1,
            },
        },
        "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0]}},
        "17": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "nice-assistant", "images": ["16", 0]},
        },
    }


def identity_object_info():
    return {
        "LoadImage": {
            "display_name": "Persona reference",
            "input": {"required": {"image": [["existing.png"], {"image_upload": True}]}},
            "output": ["IMAGE"],
        },
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["base.safetensors"]]}},
            "output": ["MODEL"],
        },
        "IPAdapterAdvanced": {
            "input": {"required": {"image": ["IMAGE"], "model": ["MODEL"]}},
            "output": ["MODEL"],
        },
        "EmptyLatentImage": {
            "input": {
                "required": {
                    "width": ["INT"],
                    "height": ["INT"],
                    "batch_size": ["INT"],
                }
            },
            "output": ["LATENT"],
        },
        "CLIPTextEncode": {
            "input": {"required": {"text": ["STRING"]}},
            "output": ["CONDITIONING"],
        },
        "KSampler": {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "positive": ["CONDITIONING"],
                    "negative": ["CONDITIONING"],
                    "latent_image": ["LATENT"],
                    "seed": ["INT"],
                }
            },
            "output": ["LATENT"],
        },
        "VAEDecode": {
            "input": {"required": {"samples": ["LATENT"]}},
            "output": ["IMAGE"],
        },
        "SaveImage": {
            "input": {"required": {"filename_prefix": ["STRING"], "images": ["IMAGE"]}},
            "output": [],
            "output_node": True,
        },
    }


class ProviderReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.running.create_and_login()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_ollama_health_uses_provider_contract(self):
        response = self.running.client.post("/api/v1/provider-checks", json={"provider": "ollama"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["status"], "ready")

    def test_openai_missing_key_and_safe_http_error(self):
        missing = self.running.client.post("/api/v1/provider-checks", json={"provider": "openai"})
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json()["status"], "missing")
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/models",
            401,
            "Unauthorized sk-secret-value",
            {},
            io.BytesIO(b'{"error":{"message":"bad key sk-secret-value"}}'),
        )
        with mock.patch("app.provider_service.urllib.request.urlopen", side_effect=error):
            failed = self.running.client.post(
                "/api/v1/provider-checks",
                json={"provider": "openai", "settings": {"openai_api_key": "sk-test-value"}},
            )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertNotIn("sk-secret", str(failed.json()))

    def test_local_provider_aliases_and_unknown_provider(self):
        with mock.patch("app.provider_service.provider_get_json", return_value={"data": [{"id": "af_heart"}]}):
            kokoro = self.running.client.post(
                "/api/v1/provider-checks",
                json={"provider": "kokoro", "settings": {"preferences": {}}},
            )
            automatic = self.running.client.post("/api/v1/provider-checks", json={"provider": "a1111"})
        self.assertTrue(kokoro.json()["ok"])
        self.assertEqual(automatic.json()["provider"], "automatic1111")
        self.assertEqual(
            self.running.client.post("/api/v1/provider-checks", json={"provider": "unknown"}).status_code,
            404,
        )

    def test_comfyui_workflow_inspection_reports_provider_compatibility_without_claiming_live_success(self):
        with mock.patch(
            "app.provider_service.provider_get_json",
            return_value=identity_object_info(),
        ) as provider_get:
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={
                    "workflow_patch": identity_workflow(),
                    "settings": {"image_local_api_auth": "operator:password"},
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "provider_compatible")
        self.assertTrue(result["provider_compatible"])
        self.assertFalse(result["live_tested"])
        self.assertEqual(
            result["identity_input_candidates"],
            [{"node_id": "10", "input_name": "image", "label": "Persona reference (node 10)"}],
        )
        self.assertEqual(result["asset_checks"][0]["available"], True)
        self.assertIn("live generation test", result["message"].lower())
        self.assertTrue(provider_get.call_args.args[0].endswith("/object_info"))
        self.assertEqual(
            provider_get.call_args.kwargs["headers"]["Authorization"],
            "Basic b3BlcmF0b3I6cGFzc3dvcmQ=",
        )
        self.assertEqual(provider_get.call_args.kwargs["max_bytes"], 16_000_000)

    def test_comfyui_workflow_inspection_identifies_missing_nodes_and_model_assets(self):
        workflow = identity_workflow()
        workflow["11"]["inputs"]["ckpt_name"] = "missing.safetensors"
        workflow["12"]["class_type"] = "NotInstalledIdentityNode"
        object_info = identity_object_info()
        with mock.patch("app.provider_service.provider_get_json", return_value=object_info):
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": workflow, "settings": {}},
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "incompatible")
        self.assertFalse(result["provider_compatible"])
        self.assertEqual(result["missing_node_types"], ["NotInstalledIdentityNode"])
        self.assertEqual(
            [(item["input_name"], item["available"]) for item in result["asset_checks"]],
            [("ckpt_name", False)],
        )

    def test_comfyui_workflow_inspection_keeps_disconnected_reference_as_draft(self):
        workflow = identity_workflow()
        workflow["12"]["inputs"]["image"] = "existing.png"
        with mock.patch("app.provider_service.provider_get_json", return_value=identity_object_info()):
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": workflow},
            )
        result = response.json()
        self.assertEqual(result["status"], "incompatible")
        self.assertFalse(result["provider_compatible"])
        self.assertEqual(result["identity_input_candidates"], [])
        self.assertIn("valid path", " ".join(result["warnings"]))

    def test_comfyui_workflow_inspection_rejects_broken_links(self):
        workflow = identity_workflow()
        workflow["15"]["inputs"]["model"] = ["99", 0]
        with mock.patch("app.provider_service.provider_get_json", return_value=identity_object_info()):
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": workflow},
            )
        result = response.json()
        self.assertFalse(result["provider_compatible"])
        self.assertIn("links to missing node 99", " ".join(result["warnings"]))

    def test_comfyui_workflow_inspection_rejects_missing_required_inputs(self):
        workflow = identity_workflow()
        del workflow["15"]["inputs"]["positive"]
        with mock.patch("app.provider_service.provider_get_json", return_value=identity_object_info()):
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": workflow},
            )
        result = response.json()
        self.assertFalse(result["provider_compatible"])
        self.assertIn("missing required input(s): positive", " ".join(result["warnings"]))

    def test_comfyui_workflow_inspection_returns_safe_provider_errors(self):
        error = urllib.error.HTTPError(
            "http://provider/object_info",
            500,
            "secret provider detail",
            {},
            io.BytesIO(b"secret provider detail"),
        )
        with mock.patch("app.provider_service.provider_get_json", side_effect=error):
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": identity_workflow()},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "error")
        self.assertNotIn("secret provider detail", str(response.json()))

    def test_comfyui_workflow_inspection_rejects_empty_workflow_without_contacting_provider(self):
        with mock.patch("app.provider_service.provider_get_json") as provider_get:
            response = self.running.client.post(
                "/api/v1/media-catalog/identity-workflows/inspect",
                json={"workflow_patch": {}},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "invalid")
        self.assertFalse(response.json()["provider_compatible"])
        provider_get.assert_not_called()

    def test_provider_json_reader_bounds_object_metadata(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = b'{"too":"large"}'
        with mock.patch("app.providers.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "size limit"):
                provider_get_json("http://provider/object_info", max_bytes=8)


if __name__ == "__main__":
    unittest.main()
