"""Video through the operator's own ComfyUI, end to end.

Sora's API shuts down on 2026-09-24 and no surviving cloud video service
accepts this product's content, so video became local-only by decision. These
pin the new path: the adapter runs a cataloged video workflow and returns a
real container, a video without a cataloged workflow is refused in plain
words rather than rendered through a graph nobody chose, and a chat's video
request travels planner -> approval -> ComfyUI -> stored mp4.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.capability_service import CAPABILITY_PLANNING
from app.media_clients import comfyui_video
from app.media_adapters import LocalVideoProvider
from app.provider_contracts import CancellationToken, MediaRequest
from tests.support import TestApp
from tests.test_capabilities import FakeChatProvider

VIDEO_GRAPH = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved words"}},
    "3": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "nice-assistant"}},
}


def _comfy_transport(submitted: dict):
    def transport(request, timeout=0, **_kwargs):
        class Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def close(self):
                return None

            def read(self, *_size):
                url = request.full_url
                if url.endswith("/prompt"):
                    submitted["graph"] = json.loads(request.data.decode())["prompt"]
                    return json.dumps({"prompt_id": "p1"}).encode()
                if "/history/" in url:
                    return json.dumps(
                        {
                            "p1": {
                                "outputs": {
                                    "3": {"gifs": [{"filename": "clip_00001.mp4", "subfolder": "", "type": "output"}]}
                                }
                            }
                        }
                    ).encode()
                if "/view" in url:
                    return b"MP4-BYTES"
                raise AssertionError(f"unexpected URL {url}")

        return Response()

    return transport


class LocalVideoAdapterTests(unittest.TestCase):
    def test_a_cataloged_workflow_becomes_a_real_video_file(self):
        submitted: dict = {}
        options = {
            "backend": "comfyui",
            "base_url": "http://127.0.0.1:8188",
            "size": "1280x704",
            "local_settings": {
                "workflow_patch": VIDEO_GRAPH,
                "prompt_bindings": [{"node_id": "2", "input_name": "text"}],
                "compiled_prompt": "a lighthouse in a storm",
                "compiled_negative": "",
            },
        }
        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=_comfy_transport(submitted)):
            artifact = LocalVideoProvider().generate(
                MediaRequest("video", "a lighthouse in a storm", options), CancellationToken()
            )

        self.assertEqual(artifact.kind, "video")
        self.assertEqual(artifact.extension, ".mp4")
        self.assertEqual(artifact.content_type, "video/mp4")
        self.assertEqual(artifact.content, b"MP4-BYTES")
        # The request landed in the bound node, not wherever the export had text.
        self.assertEqual(submitted["graph"]["2"]["inputs"]["text"], "a lighthouse in a storm")

    def test_video_without_a_cataloged_workflow_is_refused_plainly(self):
        with self.assertRaises(ValueError) as caught:
            comfyui_video("anything", "1280x704", "http://127.0.0.1:8188", {"workflow_patch": {}})

        self.assertIn("cataloged ComfyUI video workflow", str(caught.exception))

    def test_planner_names_local_video_as_a_local_provider(self):
        from app.media_planner import RUNTIME_OPERATIONS, _execution_options

        self.assertIn(("local-video", "comfyui"), RUNTIME_OPERATIONS)

        class Preset:
            id = "preset-1"
            name = "Wan clips"
            revision = 1
            priority = 50

        snapshots = [
            {
                "id": "model-1",
                "resource_type": "model",
                "provider_key": "local-video",
                "backend": "comfyui",
                "external_id": "wan2.2_ti2v_5B_fp16.safetensors",
                "default_settings": {},
            }
        ]
        winner = {"definition": {"base_model_resource_id": "model-1"}, "stages": [], "loras": []}

        options = _execution_options(Preset(), winner, snapshots)

        self.assertEqual(options["provider"], "local")
        self.assertEqual(options["backend"], "comfyui")


class LocalVideoJourneyTests(unittest.TestCase):
    """A chat's video request, planned and executed locally."""

    def test_chat_video_runs_through_the_catalog_and_comfyui(self):
        provider = FakeChatProvider(
            ["I can create that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_video",
                            "scene": {
                                "subject": "a lighthouse in a storm",
                                "action": "",
                                "setting": "",
                                "wardrobe": "",
                                "framing": "",
                                "lighting": "",
                                "camera": "",
                                "mood": "",
                            },
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        submitted: dict = {}
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            # The real adapter, over a faked wire: the harness registry starts
            # empty and each test registers exactly what it exercises.
            running.services.providers.media_providers["local-video"] = LocalVideoProvider()
            settings = running.client.get("/api/v1/settings").json()
            preferences = {**settings["preferences"], "video_provider": "local"}
            running.client.put("/api/v1/settings", json={**settings, "preferences": preferences})
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "video",
                    "name": "Wan 2.2",
                    "provider_key": "local-video",
                    "backend": "comfyui",
                    "external_id": "wan2.2_ti2v_5B_fp16.safetensors",
                    "enabled": True,
                    "priority": 50,
                    "operations": ["generate"],
                    "domains": [],
                    "content_tags": ["general"],
                    "features": ["text_to_image"],
                    "estimated_vram_mb": 0,
                    "estimated_load_seconds": 0,
                    "default_settings": {},
                    "notes": "",
                    "compatible_model_ids": [],
                },
            )
            self.assertEqual(model.status_code, 201, model.text)
            workflow = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "workflow",
                    "kind": "video",
                    "name": "Wan text to video",
                    "provider_key": "local-video",
                    "backend": "comfyui",
                    "external_id": "wan-t2v",
                    "enabled": True,
                    "priority": 50,
                    "operations": ["generate"],
                    "domains": [],
                    "content_tags": [],
                    "features": [],
                    "estimated_vram_mb": 0,
                    "estimated_load_seconds": 0,
                    "default_settings": {
                        "workflow_patch": VIDEO_GRAPH,
                        "prompt_bindings": [{"node_id": "2", "input_name": "text"}],
                    },
                    "notes": "",
                    "compatible_model_ids": [model.json()["id"]],
                },
            )
            self.assertEqual(workflow.status_code, 201, workflow.text)
            # The lazy preset pass pairs the model with a recipe.
            running.client.get("/api/v1/media-catalog")

            chat = running.client.post("/api/v1/chats", json={}).json()
            with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=_comfy_transport(submitted)):
                accepted = running.client.post(
                    f"/api/v1/chats/{chat['id']}/turns",
                    json={"text": "Make me a video of a lighthouse in a storm", "memory_mode": "off"},
                ).json()
                running.wait_job(accepted["job"]["id"])
                pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()[
                    "items"
                ][0]
                self.assertEqual(pending["status"], "pending_confirmation", pending)
                approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
                self.assertEqual(approved.status_code, 200, approved.text)
                job = running.wait_job(approved.json()["job_id"])

            self.assertEqual(job["status"], "completed", job)
            completed = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            media = running.client.get(f"/api/v1/media/{completed['result']['mediaId']}")
            self.assertEqual(media.content, b"MP4-BYTES")
            # The prompt the chat asked for reached the bound node.
            self.assertIn("lighthouse", submitted["graph"]["2"]["inputs"]["text"])


if __name__ == "__main__":
    unittest.main()
