import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from app.media_journal import REDACTED, basename_only, encode_detail, redact, render_export
from app.models import MediaFile
from app.provider_contracts import MediaArtifact
from app.repositories import UnitOfWork
from tests.support import TestApp


ROOT = Path(__file__).resolve().parents[1]


def _audit_module():
    spec = importlib.util.spec_from_file_location("audit_public_repo", ROOT / "scripts" / "audit_public_repo.py")
    module = importlib.util.module_from_spec(spec)
    # Register before executing so the module's dataclasses can resolve their
    # own annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class JournalRedactionTests(unittest.TestCase):
    def test_credentials_addresses_and_paths_never_reach_a_stored_journal(self):
        detail = redact(
            {
                "provider": "local",
                "options": {
                    "api_key": "not-a-real-key-for-unit-tests",
                    "api_auth": "owner:hunter2",
                    "base_url": "http://comfy-host.invalid:8188",
                    "backend": "comfyui",
                    "local_settings": {
                        "identity_reference_path": "/data/identity_references/owner_reference.jpg",
                        "steps": 28,
                    },
                },
            }
        )
        options = detail["options"]
        self.assertEqual(options["api_key"], REDACTED)
        self.assertEqual(options["api_auth"], REDACTED)
        self.assertEqual(options["base_url"], REDACTED)
        # The backend is what a reader actually needs, and it survives.
        self.assertEqual(options["backend"], "comfyui")
        self.assertEqual(options["local_settings"]["identity_reference_path"], "owner_reference.jpg")
        self.assertEqual(options["local_settings"]["steps"], 28)

    def test_absolute_paths_inside_free_text_are_reduced_to_names(self):
        message = redact("failed writing /data/images/owner_9f2c.png during save")
        self.assertNotIn("/data/images", message)
        self.assertIn("owner_9f2c.png", message)

    def test_windows_and_posix_locations_both_reduce_to_a_basename(self):
        self.assertEqual(basename_only(r"C:\\Users\\owner\\images\\shot.png"), "shot.png")
        self.assertEqual(basename_only("/data/images/shot.png"), "shot.png")

    def test_oversized_detail_is_replaced_rather_than_stored(self):
        encoded = encode_detail({"blob": "x" * 200_000})
        self.assertIn("truncated", encoded)
        self.assertNotIn("x" * 5_000, encoded)

    def test_structures_are_bounded_so_one_stage_cannot_break_a_journal(self):
        deep = current = {}
        for _ in range(30):
            current["next"] = {}
            current = current["next"]
        rendered = redact(deep)
        self.assertIn("truncated", str(rendered))
        wide = redact(list(range(500)))
        self.assertLessEqual(len(wide), 51)


class JournalExportTests(unittest.TestCase):
    def _journal(self) -> dict:
        return {
            "id": "journal123",
            "kind": "image",
            "origin": "conversation",
            "status": "completed",
            "media_id": "media123",
            "media_plan_id": "plan123",
            "started_at": 1_760_000_000,
            "completed_at": 1_760_000_012,
            "duration_ms": 12_000,
            "error": None,
            "stages": [
                {
                    "sequence": 1,
                    "stage": "provider_request",
                    "status": "ok",
                    "summary": "submitting to local",
                    "detail": {"provider": "local", "options": {"api_key": REDACTED, "backend": "comfyui"}},
                    "started_at": 1_760_000_001,
                    "duration_ms": 900,
                }
            ],
        }

    def test_export_is_one_readable_document_naming_what_happened(self):
        text = render_export(self._journal())
        self.assertIn("# Generation journal journal123", text)
        self.assertIn("provider_request", text)
        self.assertIn("submitting to local", text)
        self.assertIn("comfyui", text)

    def test_export_passes_the_public_repository_privacy_audit(self):
        audit = _audit_module()
        findings = audit.audit_text("generation-journal-export.md", render_export(self._journal()), [])
        self.assertEqual([finding.message for finding in findings], [])


class JournalGenerationTests(unittest.TestCase):
    def _login_with_local_image(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        saved = running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)

    def test_a_direct_image_generation_writes_exactly_one_readable_journal(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a quiet harbour at dawn"})
            self.assertEqual(started.status_code, 202, started.text)
            job = running.wait_job(started.json()["job_id"])
            self.assertEqual(job["status"], "completed", job)
            media_id = job["result"]["mediaId"]

            journal = running.client.get(f"/api/v1/media/{media_id}/journal")
            self.assertEqual(journal.status_code, 200, journal.text)
            body = journal.json()
            self.assertEqual(body["status"], "completed")
            self.assertEqual(body["media_id"], media_id)
            stages = [stage["stage"] for stage in body["stages"]]
            self.assertIn("request", stages)
            self.assertIn("provider_request", stages)
            self.assertIn("provider_response", stages)
            self.assertIn("stored", stages)

            listed = running.client.get("/api/v1/media-journals").json()["items"]
            self.assertEqual([item["media_id"] for item in listed], [media_id])

    def test_the_journal_records_the_prompt_that_was_actually_submitted(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a quiet harbour at dawn"})
            media_id = running.wait_job(started.json()["job_id"])["result"]["mediaId"]
            body = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            submitted = next(stage for stage in body["stages"] if stage["stage"] == "provider_request")
            self.assertIn("quiet harbour at dawn", submitted["detail"]["prompt"])

    def test_the_export_route_returns_a_named_downloadable_document(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a lantern in fog"})
            media_id = running.wait_job(started.json()["job_id"])["result"]["mediaId"]
            journal_id = running.client.get(f"/api/v1/media/{media_id}/journal").json()["id"]

            exported = running.client.get(f"/api/v1/media-journals/{journal_id}/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertIn("text/markdown", exported.headers["content-type"])
            self.assertIn(f"generation-journal-{journal_id}.md", exported.headers["content-disposition"])
            self.assertIn("# Generation journal", exported.text)

    def test_a_journal_belongs_to_its_owner_alone(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a red door"})
            media_id = running.wait_job(started.json()["job_id"])["result"]["mediaId"]
            journal_id = running.client.get(f"/api/v1/media/{media_id}/journal").json()["id"]

            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            self.assertEqual(running.client.get(f"/api/v1/media/{media_id}/journal").status_code, 404)
            self.assertEqual(running.client.get(f"/api/v1/media-journals/{journal_id}").status_code, 404)
            self.assertEqual(running.client.get("/api/v1/media-journals").json()["items"], [])

    def test_a_failing_journal_never_costs_the_operator_the_picture(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)

            def explode(**_kwargs):
                raise RuntimeError("journal storage is unavailable")

            running.services.media_journal.start = explode

            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a bicycle in rain"})
            job = running.wait_job(started.json()["job_id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertTrue(job["result"]["mediaId"])

    def test_deleting_an_image_deletes_its_journal(self):
        # There is no per-image delete route today; media is removed with its
        # chat or through bulk data actions. What must hold either way is that
        # a journal never outlives the artifact it describes.
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._login_with_local_image(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a paper boat"})
            media_id = running.wait_job(started.json()["job_id"])["result"]["mediaId"]
            journal_id = running.client.get(f"/api/v1/media/{media_id}/journal").json()["id"]

            services = running.services
            with UnitOfWork(services.runtime.session_factory, services.runtime.secret_store) as uow:
                uow.session.delete(uow.session.get(MediaFile, media_id))

            self.assertEqual(running.client.get(f"/api/v1/media-journals/{journal_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
