from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile

from PIL import Image

from app.identity_contracts import IdentityVerificationResult
from app.provider_contracts import ProviderHealth, ProviderStatus
from app.repositories import UnitOfWork
from tests.support import TestApp


def test_image(color=(180, 80, 70), size=(256, 256)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class FakeIdentityProvider:
    name = "compreface"

    def __init__(self, score=0.91, gate=None):
        self.score = score
        self.gate = gate
        self.requests = []
        self.started = threading.Event()

    def health(self, base_url, api_key, timeout_seconds):
        self.health_values = (base_url, api_key, timeout_seconds)
        return ProviderHealth(self.name, ProviderStatus.READY, "Fake verifier ready.", 2)

    def verify(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        self.started.set()
        if self.gate:
            while not self.gate.wait(0.01):
                cancellation.raise_if_cancelled()
        return IdentityVerificationResult(self.score, 1, 1, "fake-v1", "request-safe")


class PersonaIdentityTests(unittest.TestCase):
    def _persona(self, running):
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Identity"}).json()
        response = running.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace["id"], "name": "Avery"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _configure(self, running):
        response = running.client.put(
            "/api/v1/identity-validation/settings",
            json={
                "provider": "compreface",
                "base_url": "http://verifier.lan:8000",
                "api_key": "secret-verifier-key",
                "timeout_seconds": 12,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["api_key"], "********-key")

    def _approved_reference(self, running, persona_id):
        consent = running.client.post(
            f"/api/v1/personas/{persona_id}/visual-identity/consent",
            json={"attested": True},
        )
        self.assertEqual(consent.status_code, 200, consent.text)
        uploaded = running.client.post(
            f"/api/v1/personas/{persona_id}/visual-identity/references",
            files={"file": ("reference.png", test_image(), "image/png")},
            data={"provenance": "user_upload", "attested": "true"},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        reference = uploaded.json()
        approved = running.client.post(f"/api/v1/identity-references/{reference['id']}/approval")
        self.assertEqual(approved.status_code, 200, approved.text)
        return approved.json()

    def _candidate(self, running, user_id):
        target = running.config.image_dir / "candidate.png"
        target.write_bytes(test_image((70, 90, 180)))
        with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
            media = uow.repo.add_media(
                user_id=user_id,
                chat_id=None,
                kind="image",
                filename=target.name,
                local_path=str(target),
            )
            return media.id

    def test_consent_reference_review_validation_and_truthful_claim(self):
        provider = FakeIdentityProvider(0.91)
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), identity_providers={"compreface": provider}) as running,
        ):
            user_id = running.create_and_login()
            persona = self._persona(running)
            profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertEqual(profile["consent_status"], "not_granted")
            self.assertEqual(profile["conditioning_fallback"], "allow_unconditioned")
            self.assertFalse(profile["generation_workflow_configured"])
            self.assertFalse(profile["verification_configured"])
            self.assertFalse(profile["validation_ready"])

            blocked = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references",
                files={"file": ("reference.png", test_image(), "image/png")},
                data={"provenance": "user_upload", "attested": "true"},
            )
            self.assertEqual(blocked.status_code, 409)

            self._configure(running)
            check = running.client.post("/api/v1/identity-validation/check")
            self.assertEqual(check.status_code, 200, check.text)
            self.assertTrue(check.json()["ready"])
            self.assertEqual(provider.health_values[1], "secret-verifier-key")
            reference = self._approved_reference(running, persona["id"])
            self.assertEqual(reference["review_status"], "approved")
            self.assertEqual(reference["content_type"], "image/jpeg")
            self.assertEqual(running.client.get(reference["content_url"]).status_code, 200)

            ready = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertTrue(ready["verification_configured"])
            self.assertTrue(ready["validation_ready"])
            backup = running.client.post("/api/v1/admin/backups", json={"include_media": True})
            self.assertEqual(backup.status_code, 200, backup.text)
            with zipfile.ZipFile(running.config.backup_dir / backup.json()["name"]) as archive:
                self.assertTrue(any(name.startswith("data/identity_references/") for name in archive.namelist()))
            metadata_backup = running.client.post("/api/v1/admin/backups", json={"include_media": False})
            self.assertEqual(metadata_backup.status_code, 200, metadata_backup.text)
            with zipfile.ZipFile(running.config.backup_dir / metadata_backup.json()["name"]) as archive:
                self.assertFalse(any(name.startswith("data/identity_references/") for name in archive.namelist()))
            candidate_id = self._candidate(running, user_id)
            accepted = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            )
            self.assertEqual(accepted.status_code, 202, accepted.text)
            job = running.wait_job(accepted.json()["job"]["id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["result"]["status"], "passed")
            self.assertAlmostEqual(job["result"]["score"], 0.91)
            status = running.client.get(f"/api/v1/media/{candidate_id}/identity-status").json()
            self.assertEqual(status["claim_status"], "verified")
            self.assertEqual(status["persona_id"], persona["id"])
            self.assertEqual(len(provider.requests), 1)

            provider.score = 0.4
            rejected = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            )
            rejected_job = running.wait_job(rejected.json()["job"]["id"])
            self.assertEqual(rejected_job["result"]["status"], "failed")
            status = running.client.get(f"/api/v1/media/{candidate_id}/identity-status").json()
            self.assertEqual(status["claim_status"], "rejected")

            updated = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "show_unverified",
                    "conditioning_fallback": "require_conditioning",
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["conditioning_fallback"], "require_conditioning")
            persisted = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity")
            self.assertEqual(persisted.json()["conditioning_fallback"], "require_conditioning")
            unverified = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            )
            unverified_job = running.wait_job(unverified.json()["job"]["id"])
            self.assertEqual(unverified_job["result"]["claim_status"], "unverified")
            status = running.client.get(f"/api/v1/media/{candidate_id}/identity-status").json()
            self.assertEqual(status["claim_status"], "unverified")

    def test_secret_is_encrypted_owner_isolation_and_withdrawal_deletes_reference(self):
        provider = FakeIdentityProvider()
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), identity_providers={"compreface": provider}) as running,
        ):
            owner_id = running.create_and_login("owner")
            persona = self._persona(running)
            self._configure(running)
            reference = self._approved_reference(running, persona["id"])
            reference_path = running.services.identity.reference_path(owner_id, reference["id"])
            self.assertTrue(reference_path.exists())
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                stored = uow.repo.identity_settings(owner_id).api_key_encrypted
            self.assertTrue(stored.startswith("enc:v1:"))
            self.assertNotIn("secret-verifier-key", stored)

            running.client.delete("/api/v1/session")
            running.create_and_login("other")
            self.assertEqual(running.client.get(reference["content_url"]).status_code, 404)
            running.client.delete("/api/v1/session")
            running.client.post("/api/v1/session", json={"username": "owner", "password": "pass1234"})

            withdrawn = running.client.delete(f"/api/v1/personas/{persona['id']}/visual-identity/consent")
            self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
            self.assertEqual(withdrawn.json()["consent_status"], "withdrawn")
            self.assertFalse(reference_path.exists())
            self.assertEqual(running.client.get(reference["content_url"]).status_code, 404)

    def test_persona_deletion_cleans_reference_files_after_database_delete(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            persona = self._persona(running)
            reference = self._approved_reference(running, persona["id"])
            reference_path = running.services.identity.reference_path(user_id, reference["id"])

            deleted = running.client.delete(f"/api/v1/personas/{persona['id']}")

            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse(reference_path.exists())
            self.assertEqual(running.client.get(reference["content_url"]).status_code, 404)

    def test_latest_media_status_is_ordered_across_personas(self):
        provider = FakeIdentityProvider(0.91)
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), identity_providers={"compreface": provider}) as running,
        ):
            user_id = running.create_and_login()
            self._configure(running)
            first_persona = self._persona(running)
            self._approved_reference(running, first_persona["id"])
            second_persona = self._persona(running)
            self._approved_reference(running, second_persona["id"])
            candidate_id = self._candidate(running, user_id)

            first = running.client.post(
                f"/api/v1/personas/{first_persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            )
            first_job = running.wait_job(first.json()["job"]["id"])
            self.assertEqual(first_job["result"]["status"], "passed")

            provider.score = 0.4
            second = running.client.post(
                f"/api/v1/personas/{second_persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            )
            second_job = running.wait_job(second.json()["job"]["id"])
            self.assertEqual(second_job["result"]["status"], "failed")

            status = running.client.get(f"/api/v1/media/{candidate_id}/identity-status").json()
            self.assertEqual(status["persona_id"], second_persona["id"])
            self.assertEqual(status["claim_status"], "rejected")
            self.assertGreater(second_job["result"]["created_order"], first_job["result"]["created_order"])

    def test_bad_images_and_disabled_provider_are_honest(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            persona = self._persona(running)
            running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/consent",
                json={"attested": True},
            )
            bad = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references",
                files={"file": ("bad.svg", b"<svg onload='x'>", "image/svg+xml")},
                data={"provenance": "user_upload", "attested": "true"},
            )
            self.assertEqual(bad.status_code, 400)
            check = running.client.post("/api/v1/identity-validation/check")
            self.assertEqual(check.status_code, 200)
            self.assertFalse(check.json()["ready"])
            self.assertEqual(check.json()["status"], "unavailable")

            oversized = running.config.image_dir / "oversized-reference.bin"
            with oversized.open("wb") as output:
                output.truncate(5 * 1024 * 1024 + 1)
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                media = uow.repo.add_media(
                    user_id=user_id,
                    chat_id=None,
                    kind="image",
                    filename=oversized.name,
                    local_path=str(oversized),
                )
            bounded = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references/from-media",
                json={"media_id": media.id, "attested": True},
            )
            self.assertEqual(bounded.status_code, 413, bounded.text)

    def test_running_validation_cancellation_is_durable_and_idempotent(self):
        gate = threading.Event()
        provider = FakeIdentityProvider(gate=gate)
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), identity_providers={"compreface": provider}) as running,
        ):
            user_id = running.create_and_login()
            persona = self._persona(running)
            self._configure(running)
            self._approved_reference(running, persona["id"])
            candidate_id = self._candidate(running, user_id)
            accepted = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/validations",
                json={"media_id": candidate_id},
            ).json()
            self.assertTrue(provider.started.wait(2))
            cancelled = running.client.delete(f"/api/v1/jobs/{accepted['job']['id']}")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            again = running.client.delete(f"/api/v1/jobs/{accepted['job']['id']}")
            self.assertEqual(again.json()["status"], "cancelled")
            gate.set()
            deadline = time.monotonic() + 2
            latest = None
            while time.monotonic() < deadline:
                latest = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity/validations").json()[
                    "items"
                ][0]
                if latest["status"] == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual(latest["status"], "cancelled")
            self.assertEqual(latest["claim_status"], "unverified")


if __name__ == "__main__":
    unittest.main()
