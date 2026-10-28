"""Signing in, and staying signed in on a device somebody chose to keep. ADR 0047."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
