"""Identity resemblance is structural; comparison is advisory.

ADR 0031. Resemblance comes from a declared conditioning mechanism recorded on
the persona. A comparison afterwards measures the result; it is not how the
result is achieved, and it is not required for the product to work. These tests
pin that separation, including that nothing needs a verifier to be running.
"""

from pathlib import Path
import tempfile
import unittest

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


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


def chat_provider() -> FakeChatProvider:
    return FakeChatProvider(["Here you go."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})


class IdentitySpecTests(unittest.TestCase):
    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}).json()

    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def test_a_new_profile_records_its_mechanism_and_leaves_retry_off(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            saved = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"appearance_description": "dark hair, green eyes"},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            profile = saved.json()

            self.assertEqual(profile["conditioning_mechanism"], "reference_adapter")
            # ADR 0031: resampling until a comparison passes is off unless an
            # operator deliberately switches it on.
            self.assertFalse(profile["comparison_retry_enabled"])

    def test_the_mechanism_must_be_one_the_platform_implements(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            refused = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"conditioning_mechanism": "wishful_thinking"},
            )
            self.assertEqual(refused.status_code, 422, refused.text)

    def test_an_operator_can_switch_the_retry_loop_on_deliberately(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            saved = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "comparison_retry_enabled": True,
                    "max_generation_attempts": 3,
                    "conditioning_mechanism": "identity_pass",
                },
            ).json()

            self.assertTrue(saved["comparison_retry_enabled"])
            self.assertEqual(saved["conditioning_mechanism"], "identity_pass")
            # Bounded exactly as ADR 0013 specified; demotion did not remove the
            # limit, it removed the loop being on by default.
            self.assertEqual(saved["max_generation_attempts"], 3)

    def test_a_persona_image_generates_with_no_verifier_configured(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider()) as running:
            self._ready(running)
            persona = self._persona(running)
            chat = running.client.post("/api/v1/chats", json={"persona_id": persona["id"], "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a picture of you", "memory_mode": "off"},
            ).json()
            chat_job = running.wait_job(accepted["job"]["id"])
            followup = (chat_job.get("result") or {}).get("followup_job_id")
            if followup:
                running.wait_job(followup)

            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertTrue(requests)
            job = running.wait_job(requests[0]["job_id"])
            # No verifier is configured, and that is a normal state rather than a
            # blocked one.
            self.assertEqual(job["status"], "completed", job)
            self.assertTrue(job["result"]["mediaId"])

    def test_nothing_polls_the_verifier_on_a_timer(self):
        source = Path("app").rglob("*.py")
        offenders = []
        for path in source:
            text = path.read_text(encoding="utf-8")
            if "check_provider" not in text:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if "check_provider" in stripped and ("while " in stripped or "interval" in stripped):
                    offenders.append(f"{path}: {stripped}")
        # Readiness is answered on demand. A timer would keep an optional
        # service warm, which is the standing cost ADR 0031 exists to avoid.
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
