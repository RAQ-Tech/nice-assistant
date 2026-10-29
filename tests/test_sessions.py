"""Signing in, and staying signed in on a device somebody chose to keep. ADR 0047."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from app import auth
from app.repositories import UnitOfWork
from app.resource_service import REMEMBERED_SESSION_SECONDS
from tests.support import TestApp

CREDENTIALS = {"username": "owner", "password": "pass1234"}


class SignupStatusTests(unittest.TestCase):
    def test_the_first_visit_is_told_to_create_the_first_account(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self.assertEqual(running.client.get("/api/v1/signup").json(), {"open": True, "first_account": True})
            running.client.post("/api/v1/users", json=CREDENTIALS)
            running.services.resources.allow_public_signup = False
            # Sign-in then offers nothing that would only be refused.
            self.assertEqual(running.client.get("/api/v1/signup").json(), {"open": False, "first_account": False})


class StayingSignedInTests(unittest.TestCase):
    def _signed_in(self, running: TestApp, remember: bool):
        running.client.post("/api/v1/users", json=CREDENTIALS)
        response = running.client.post("/api/v1/session", json={**CREDENTIALS, "remember": remember})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_a_kept_device_is_not_signed_out_for_being_idle(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.services.resources.session_ttl_seconds = 1
            response = self._signed_in(running, remember=True)
            # A cookie that outlives closing the browser, which is the point.
            self.assertIn(f"Max-Age={REMEMBERED_SESSION_SECONDS}", response.headers["set-cookie"])
            time.sleep(2.1)
            session = running.client.get("/api/v1/session")
            self.assertEqual(session.status_code, 200)
            self.assertTrue(session.json()["persistent"])

    def test_a_device_not_kept_still_signs_out_after_the_idle_limit(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.services.resources.session_ttl_seconds = 1
            self._signed_in(running, remember=False)
            time.sleep(2.1)
            self.assertEqual(running.client.get("/api/v1/session").status_code, 401)


class PasswordStrengthTests(unittest.TestCase):
    """Hashes carry their own strength, and an old one is strengthened at sign-in. ADR 0049."""

    def test_a_new_hash_says_how_it_was_made(self):
        stored = auth.hash_password("pass1234")
        scheme, iterations, salt, _digest = stored.split("$")
        self.assertEqual((scheme, int(iterations)), ("pbkdf2_sha256", auth.PASSWORD_ITERATIONS))
        self.assertGreaterEqual(auth.PASSWORD_ITERATIONS, 600_000)
        self.assertEqual(len(salt), 32)
        self.assertTrue(auth.verify_password("pass1234", stored))
        self.assertFalse(auth.password_needs_rehash(stored))

    def test_a_damaged_or_foreign_hash_is_refused_rather_than_trusted(self):
        for stored in ("pbkdf2_sha256$99999999999$aa$bb", "pbkdf2_sha256$0$aa$bb", "md5$x", "", "test-only$pass1234"):
            self.assertFalse(auth.verify_password("pass1234", stored), stored)
            self.assertFalse(auth.password_needs_rehash(stored), stored)

    def test_an_old_hash_still_signs_in_and_is_strengthened_then(self):
        salt = "0123456789abcdef0123456789abcdef"
        legacy = f"{salt}${auth._derive('pass1234', salt, auth.LEGACY_PASSWORD_ITERATIONS)}"
        self.assertTrue(auth.password_needs_rehash(legacy))
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            resources = running.services.resources
            resources.password_hasher = auth.hash_password
            resources.password_verifier = auth.verify_password
            resources.password_needs_rehash = auth.password_needs_rehash
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                user_id = uow.repo.create_user("owner", legacy).id

            def stored_hash():
                with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                    return uow.repo.user_by_username("owner").password_hash

            wrong = running.client.post("/api/v1/session", json={"username": "owner", "password": "pass12345"})
            self.assertEqual(wrong.status_code, 401)
            self.assertEqual(stored_hash(), legacy)

            signed_in = running.client.post("/api/v1/session", json=CREDENTIALS)
            self.assertEqual(signed_in.status_code, 200, signed_in.text)
            self.assertEqual(signed_in.json()["user_id"], user_id)
            upgraded = stored_hash()
            self.assertTrue(upgraded.startswith(f"pbkdf2_sha256${auth.PASSWORD_ITERATIONS}$"))
            self.assertFalse(auth.password_needs_rehash(upgraded))

            running.client.delete("/api/v1/session")
            self.assertEqual(running.client.post("/api/v1/session", json=CREDENTIALS).status_code, 200)
            self.assertEqual(stored_hash(), upgraded)


if __name__ == "__main__":
    unittest.main()
