"""Identity applied as a later pass, not during generation.

ADR 0031 names two mechanisms. `reference_adapter` conditions while the picture
is being made; `identity_pass` generates the picture and then replaces the face,
which is the only option for checkpoint families no adapter was trained against.

Planning already accepted a second-pass identity workflow. Execution copied its
bindings to the top level and injected them into the first pass's graph, which
has no such node, so every one of these presets failed deterministically at
upload time. These tests pin the fix and the honesty rules around it.
"""

from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


PLANNED = {
    "capability_key": "media.generate_image",
    "scene": {
        "subject": "a portrait of the selected persona",
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
    "persona_subject": True,
}


class RecordingImageProvider:
    name = "local-image"

    def __init__(self):
        self.requests = []

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return MediaArtifact("image", f"pass-{len(self.requests)}".encode(), ".png", "image/png")


def identity_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (256, 256), (150, 90, 70)).save(output, format="PNG")
    return output.getvalue()


def chat_provider() -> FakeChatProvider:
    return FakeChatProvider(["Here you go."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})


class IdentityPassTests(unittest.TestCase):
    def _ready(self, running) -> RecordingImageProvider:
        provider = RecordingImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return provider

    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = running.client.post(
            "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}
        ).json()
        running.client.put(
            f"/api/v1/personas/{persona['id']}/visual-identity",
            json={"conditioning_mechanism": "identity_pass", "conditioning_fallback": "require_conditioning"},
        )
        running.client.post(f"/api/v1/personas/{persona['id']}/visual-identity/consent", json={"attested": True})
        uploaded = running.client.post(
            f"/api/v1/personas/{persona['id']}/visual-identity/references",
            files={"file": ("avery.png", identity_image(), "image/png")},
            data={"provenance": "user_upload", "attested": "true"},
        ).json()
        running.client.post(f"/api/v1/identity-references/{uploaded['id']}/approval")
        return persona

    def _model(self, running) -> dict:
        running.client.get("/api/v1/media-catalog/presets")
        catalog = running.client.get("/api/v1/media-catalog").json()
        return next(item for item in catalog["resources"] if item["resource_type"] == "model")

    def _scene_workflow(self, running, model_id: str) -> dict:
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "workflow",
                "kind": "image",
                "name": "Scene pass",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "scene-pass",
                "operations": ["generate"],
                "default_settings": {
                    "workflow_patch": {"900": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved"}}},
                    "prompt_bindings": [{"node_id": "900", "input_name": "text"}],
                },
                "compatible_model_ids": [model_id],
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _swap_workflow(self, running, model_id: str) -> dict:
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "workflow",
                "kind": "image",
                "name": "Face pass",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "face-pass",
                "operations": ["image_to_image"],
                "features": ["identity_control"],
                "default_settings": {
                    "workflow_patch": {
                        "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
                        "2": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
                    },
                    # A face swap takes no text, so binding a prompt into it
                    # would mean writing the request into a face-index widget.
                    "consumes_prompt": False,
                    "source_image_bindings": [{"node_id": "1", "input_name": "image"}],
                    "identity_image_bindings": [{"node_id": "2", "input_name": "image"}],
                },
                "compatible_model_ids": [model_id],
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _preset(self, running, model_id: str, scene_id: str, swap_id: str):
        return running.client.post(
            "/api/v1/media-catalog/presets",
            json={
                "name": "Scene then face",
                "priority": 100,
                "definition": {
                    "base_model_resource_id": model_id,
                    "workflow_resource_id": scene_id,
                    "identity_mechanisms": ["identity_pass"],
                    "stages": [
                        {"name": "scene", "workflow_resource_id": scene_id},
                        {"name": "face", "workflow_resource_id": swap_id},
                    ],
                },
            },
        )

    def _generate(self, running, persona_id: str) -> dict:
        chat = running.client.post("/api/v1/chats", json={"persona_id": persona_id, "memory_mode": "off"}).json()
        accepted = running.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Send me a picture of you", "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        followup = (chat_job.get("result") or {}).get("followup_job_id")
        if followup:
            running.wait_job(followup)
        requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
        assert requests, "no capability request was created"
        return running.wait_job(requests[0]["job_id"])

    def _setup(self, running):
        persona = self._persona(running)
        model = self._model(running)
        scene = self._scene_workflow(running, model["id"])
        swap = self._swap_workflow(running, model["id"])
        created = self._preset(running, model["id"], scene["id"], swap["id"])
        assert created.status_code == 201, created.text
        return persona

    def test_the_reference_goes_to_the_pass_whose_graph_has_the_nodes(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            provider = self._ready(running)
            persona = self._setup(running)

            job = self._generate(running, persona["id"])

            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(len(provider.requests), 2)
            scene_settings, face_settings = (request.options["local_settings"] for request in provider.requests)
            # The whole failure: these used to be written at the top level, so
            # the first pass tried to inject the second graph's node IDs.
            self.assertEqual(scene_settings["identity_image_bindings"], [])
            self.assertEqual(face_settings["identity_image_bindings"], [{"node_id": "2", "input_name": "image"}])
            self.assertTrue(face_settings["source_image_path"])
            self.assertEqual(provider.requests[1].options["operation"], "image_to_image")

    def test_the_finished_picture_records_which_technique_made_the_face(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            persona = self._setup(running)

            job = self._generate(running, persona["id"])
            status = running.client.get(f"/api/v1/media/{job['result']['mediaId']}/identity-status").json()

            self.assertEqual(status["conditioning"]["status"], "conditioned")
            # Conditioned during generation and swapped in afterwards are
            # different artifacts, and the record says which one this is.
            self.assertEqual(status["conditioning"]["conditioning_mechanism"], "identity_pass")

    def test_a_persona_is_offered_only_the_mechanisms_this_catalog_can_apply(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            model = self._model(running)

            profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertEqual(profile["available_mechanisms"], [])

            self._swap_workflow(running, model["id"])
            profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            # A graph that can only change a picture it is handed applies the
            # face afterwards; one that can generate conditions during it.
            self.assertEqual(profile["available_mechanisms"], ["identity_pass"])

    def test_a_graph_that_takes_no_prompt_cannot_be_asked_to_generate(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            model = self._model(running)
            refused = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "workflow",
                    "kind": "image",
                    "name": "Face pass",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "face-pass",
                    "operations": ["generate", "image_to_image"],
                    "features": ["identity_control"],
                    "default_settings": {
                        "workflow_patch": {"2": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}},
                        "consumes_prompt": False,
                        "source_image_bindings": [{"node_id": "2", "input_name": "image"}],
                        "identity_image_bindings": [{"node_id": "2", "input_name": "image"}],
                    },
                    "compatible_model_ids": [model["id"]],
                },
            )

            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("cannot generate", refused.text)


if __name__ == "__main__":
    unittest.main()
