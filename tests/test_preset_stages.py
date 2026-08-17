"""Multi-pass presets.

A second pass existed only as a correction retry after a failed identity
comparison. Declaring stages makes the two-pass case a property of the recipe -
generate the scene, then apply identity to the result - rather than something
that happens only when a measurement fails. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


class RecordingImageProvider:
    name = "local-image"

    def __init__(self):
        self.requests = []

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        index = len(self.requests)
        return MediaArtifact("image", f"pass-{index}".encode(), ".png", "image/png")


def graph(node: str, class_type: str = "LoadImage") -> dict:
    return {
        node: {"class_type": class_type, "inputs": {"image": "placeholder.png"}},
        "900": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved"}},
    }


PLANNED = {
    "capability_key": "media.generate_image",
    "scene": {
        "subject": "a harbour at dusk",
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


def chat_provider() -> FakeChatProvider:
    return FakeChatProvider(["Here you go."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})


class PresetStageTests(unittest.TestCase):
    def _ready(self, running) -> RecordingImageProvider:
        provider = RecordingImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return provider

    def _generate(self, running) -> str:
        """Generate through the conversational path, which is coordinator-planned.

        The direct button records a manual plan by design, so it never carries
        preset stages.
        """

        chat = running.client.post("/api/v1/chats", json={"title": "Harbour", "memory_mode": "off"}).json()
        accepted = running.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Send me a picture of a harbour", "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        followup = (chat_job.get("result") or {}).get("followup_job_id")
        if followup:
            running.wait_job(followup)
        requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
        assert requests, "no capability request was created"
        return running.wait_job(requests[0]["job_id"])["result"]["mediaId"]

    def _model(self, running) -> dict:
        running.client.get("/api/v1/media-catalog/presets")
        catalog = running.client.get("/api/v1/media-catalog").json()
        return next(item for item in catalog["resources"] if item["resource_type"] == "model")

    def _workflow(self, running, model_id: str, *, name: str, node: str, source: bool, vram: int = 0) -> dict:
        settings = {
            "workflow_patch": graph(node),
            "prompt_bindings": [{"node_id": "900", "input_name": "text"}],
        }
        if source:
            settings["source_image_bindings"] = [{"node_id": node, "input_name": "image"}]
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "workflow",
                "kind": "image",
                "name": name,
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": name.lower().replace(" ", "-"),
                "operations": ["generate", "image_to_image"] if source else ["generate"],
                "estimated_vram_mb": vram,
                "default_settings": settings,
                "compatible_model_ids": [model_id],
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _preset(self, running, model_id: str, stages: list[dict], **overrides) -> dict:
        payload = {
            "name": "Two pass",
            "priority": 100,
            "definition": {
                "base_model_resource_id": model_id,
                "workflow_resource_id": stages[0]["workflow_resource_id"],
                "stages": stages,
            },
        }
        payload.update(overrides)
        return running.client.post("/api/v1/media-catalog/presets", json=payload)

    def test_a_two_stage_preset_runs_both_passes_in_order(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            provider = self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False)
            detail = self._workflow(running, model["id"], name="Detail pass", node="101", source=True)
            created = self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": detail["id"]},
                ],
            )
            self.assertEqual(created.status_code, 201, created.text)

            self._generate(running)

            # Two provider submissions, and the second is the editing operation
            # that receives the first pass's picture.
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(provider.requests[1].options["operation"], "image_to_image")
            self.assertTrue(provider.requests[1].options["local_settings"]["source_image_path"])

    def test_a_binding_a_later_pass_does_not_declare_does_not_survive_into_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            provider = self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False)
            detail = self._workflow(running, model["id"], name="Detail pass", node="101", source=True)
            # Only the first graph has node 900 bound for width, so if the
            # second inherited it the injection would target a node that is not
            # in its graph and the pass would fail at submit time.
            self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": detail["id"]},
                ],
            )

            self._generate(running)

            first, second = (request.options["local_settings"] for request in provider.requests)
            self.assertEqual(first["prompt_bindings"], [{"node_id": "900", "input_name": "text"}])
            self.assertEqual(second["prompt_bindings"], [{"node_id": "900", "input_name": "text"}])
            # Assigned per pass, never merged: the second graph has no source
            # binding of the first's, and the first has none of the second's.
            self.assertEqual(first["source_image_bindings"], [])
            self.assertEqual(second["source_image_bindings"], [{"node_id": "101", "input_name": "image"}])

    def test_each_stage_records_its_own_journal_entry(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False)
            detail = self._workflow(running, model["id"], name="Detail pass", node="101", source=True)
            self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": detail["id"]},
                ],
            )
            media_id = self._generate(running)

            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            stage = next(item for item in journal["stages"] if item["stage"] == "stage_2")
            self.assertEqual(stage["detail"]["stage"], "detail")

    def test_only_the_final_pass_becomes_a_picture_in_the_library(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False)
            detail = self._workflow(running, model["id"], name="Detail pass", node="101", source=True)
            self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": detail["id"]},
                ],
            )
            self._generate(running)

            # An intermediate pass is working state, not something the owner
            # asked for, so it never reaches the library or the disk.
            library = running.client.get("/api/v1/media").json()["items"]
            self.assertEqual(len(library), 1)
            images = Path(tmp) / "data" / "images"
            self.assertEqual([item.name for item in images.glob("stage_*")], [])

    def test_a_later_stage_must_be_able_to_receive_the_previous_picture(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False)
            unusable = self._workflow(running, model["id"], name="No source", node="102", source=False)
            refused = self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": unusable["id"]},
                ],
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("source image binding", refused.text)

    def test_sequential_stages_are_costed_as_the_largest_not_the_sum(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            model = self._model(running)
            base = self._workflow(running, model["id"], name="Base pass", node="100", source=False, vram=2000)
            detail = self._workflow(running, model["id"], name="Detail pass", node="101", source=True, vram=3000)
            self._preset(
                running,
                model["id"],
                [
                    {"name": "base", "workflow_resource_id": base["id"]},
                    {"name": "detail", "workflow_resource_id": detail["id"]},
                ],
            )
            preview = running.client.post(
                "/api/v1/media-catalog/plan-previews",
                json={
                    "kind": "image",
                    "operation": "generate",
                    "domains": [],
                    "content_tags": [],
                    "required_features": [],
                },
            ).json()

            base_estimate = next(
                item["estimated_vram_mb"] for item in preview["selected_resources"] if item["resource_type"] == "model"
            )
            # ADR 0013: stages never coexist, so 2000 + 3000 must not be summed.
            self.assertEqual(preview["estimated_vram_mb"], base_estimate + 3000)


if __name__ == "__main__":
    unittest.main()
