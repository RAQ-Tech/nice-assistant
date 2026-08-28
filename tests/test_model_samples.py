"""A model's face: the sample picture and the tuning that renders it.

The models list was words about looks; a thumbnail answers "what does this
one look like?" faster than any field. These pin the two server halves the
button relies on: a model resource may carry its one sample_media_id, and a
direct image job honors per-call tuning so the sample renders with exactly
the values on the page rather than whatever the global preferences say.
"""

from pathlib import Path
import tempfile
import unittest

from tests.support import TestApp
from tests.test_capabilities import FakeImageProvider


class ModelSampleTests(unittest.TestCase):
    def test_a_model_keeps_its_one_sample_picture(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            created = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "image",
                    "name": "Sampled",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "sampled.safetensors",
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
            ).json()
            created["default_settings"] = {"sample_media_id": "abc123"}
            payload = {
                key: value
                for key, value in created.items()
                if key
                not in {
                    "id",
                    "revision",
                    "created_at",
                    "updated_at",
                    "needs_binding_review",
                    "source_template_id",
                    "source_template_version",
                }
            }
            saved = running.client.put(
                f"/api/v1/media-catalog/resources/{created['id']}",
                json=payload,
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["default_settings"]["sample_media_id"], "abc123")

    def test_a_direct_job_renders_with_the_callers_exact_numbers(self):
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111", "image_local_steps": "28"}},
            )
            accepted = running.client.post(
                "/api/v1/media/image-jobs",
                json={
                    "prompt": "a cozy reading nook",
                    "provider": "local/automatic1111",
                    "steps": 42,
                    "cfg_scale": 5.5,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                },
            )
            self.assertEqual(accepted.status_code, 202, accepted.text)
            running.wait_job(accepted.json()["job_id"])

        request = image_provider.requests[0]
        local = request.options["local_settings"]
        # The page's numbers, not the preferences': a sample that rendered
        # with different values would be a picture of the wrong settings.
        self.assertEqual(local["steps"], 42)
        self.assertEqual(local["cfg_scale"], 5.5)
        self.assertEqual(local["sampler_name"], "dpmpp_2m")
        self.assertEqual(local["scheduler"], "karras")


if __name__ == "__main__":
    unittest.main()
