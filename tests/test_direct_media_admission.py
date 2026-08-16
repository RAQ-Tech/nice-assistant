"""A direct image action declares its demand, so admission applies to it.

The direct buttons submit explicit provider settings and are not chosen by the
coordinator, which is unchanged. What changed is that their demand is no longer
unknown by default: the model they name is looked up in the catalog and its
recorded estimate is carried onto the plan. Unknown demand cannot pass
measured-capacity admission, so a direct action used to slip past it.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact
from tests.support import TestApp


class ImageProvider:
    name = "local-image"

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class DirectMediaAdmissionTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = ImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        models = [
            item
            for item in running.client.get("/api/v1/media-catalog").json()["resources"]
            if item["resource_type"] == "model" and item["kind"] == "image"
        ]
        assert models, "no image model was bootstrapped"
        # A bootstrapped model has no estimate; operators measure and record
        # them. Setting one here is what gives the direct action something to
        # carry.
        writable = {
            key: models[0][key]
            for key in (
                "resource_type",
                "kind",
                "name",
                "provider_key",
                "backend",
                "external_id",
                "enabled",
                "priority",
                "operations",
                "domains",
                "content_tags",
                "features",
                "estimated_load_seconds",
                "default_settings",
                "notes",
            )
        }
        updated = running.client.put(
            f"/api/v1/media-catalog/resources/{models[0]['id']}",
            json={**writable, "estimated_vram_mb": 6500},
        )
        assert updated.status_code == 200, updated.text
        return updated.json()

    def _direct(self, running, **values) -> dict:
        payload = {"prompt": "a garden at dusk"}
        payload.update(values)
        response = running.client.post("/api/v1/media/image-jobs", json=payload)
        assert response.status_code in {200, 201, 202}, response.text
        return response.json()

    def _plan(self, running, request_id: str) -> dict:
        return running.client.get(f"/api/v1/capability-requests/{request_id}").json()["media_plan"]

    def test_a_direct_action_carries_the_catalog_estimate_for_its_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._ready(running)
            self.assertGreater(model["estimated_vram_mb"], 0, "the fixture model has no estimate to carry")

            started = self._direct(running, model=model["external_id"])
            running.wait_job(started["job_id"])

            plan = self._plan(running, started["capability_request_id"])
            # Zero is what used to be recorded, and zero is what skips
            # measured-capacity admission entirely.
            self.assertEqual(plan["estimated_vram_mb"], model["estimated_vram_mb"])
            self.assertIn("measured capacity", plan["explanation"]["summary"])

    def test_a_model_the_catalog_never_saw_says_so_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)

            started = self._direct(running, model="a-model-nobody-added.safetensors")
            running.wait_job(started["job_id"])

            plan = self._plan(running, started["capability_request_id"])
            self.assertEqual(plan["estimated_vram_mb"], 0)
            warnings = " ".join(plan["explanation"]["warnings"])
            # An invented estimate is one the coordinator would then enforce, so
            # the gap is stated rather than filled.
            self.assertIn("demand is unknown", warnings)
            self.assertIn("Add the model to the media catalog", warnings)

    def test_a_direct_action_is_still_the_operator_settings_not_a_preset(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._ready(running)

            started = self._direct(running, model=model["external_id"])
            running.wait_job(started["job_id"])

            plan = self._plan(running, started["capability_request_id"])
            self.assertEqual(plan["source"], "manual")
            self.assertTrue(
                any("not selected by the media coordinator" in item for item in plan["explanation"]["warnings"])
            )

    def test_the_named_model_is_matched_exactly(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            model = self._ready(running)

            # A near-match must not attach one model's measurement to another.
            started = self._direct(running, model=f"{model['external_id']}-v2")
            running.wait_job(started["job_id"])

            self.assertEqual(self._plan(running, started["capability_request_id"])["estimated_vram_mb"], 0)


if __name__ == "__main__":
    unittest.main()
