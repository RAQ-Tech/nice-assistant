"""Which recipe suits a persona is persona-specific knowledge.

Routing scores presets the same way for everyone. Whether a recipe's identity
conditioning actually holds for one particular face is not something a score can
represent, so it is recorded. See ADR 0030 and ADR 0031.
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
        "subject": "a portrait",
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


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class PersonaPresetPreferenceTests(unittest.TestCase):
    def _ready(self, running):
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}).json()

    def _second_preset(self, running) -> dict:
        running.client.get("/api/v1/media-catalog/presets")
        catalog = running.client.get("/api/v1/media-catalog").json()
        model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
        created = running.client.post(
            "/api/v1/media-catalog/presets",
            json={
                "name": "Avery portrait",
                # Deliberately the lowest priority, so only a preference can
                # make it win.
                "priority": 1,
                "routing_card": "",
                "definition": {"base_model_resource_id": model["id"]},
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def _preview(self, running, persona_id: str) -> dict:
        return running.client.post(
            "/api/v1/media-catalog/plan-previews",
            json={
                "kind": "image",
                "operation": "generate",
                "domains": [],
                "content_tags": [],
                "required_features": [],
                "persona_id": persona_id,
            },
        ).json()

    def test_a_persona_records_which_recipes_work_for_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            preset = self._second_preset(running)

            saved = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"preferred_preset_ids": [preset["id"]]},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["preferred_preset_ids"], [preset["id"]])

    def test_a_persona_expresses_no_preference_until_one_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertEqual(profile["preferred_preset_ids"], [])

    def test_a_preference_wins_over_the_deterministic_score(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            preset = self._second_preset(running)

            without = self._preview(running, persona["id"])
            self.assertNotEqual(without["explanation"]["preset"]["name"], "Avery portrait")

            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"preferred_preset_ids": [preset["id"]]},
            )
            with_preference = self._preview(running, persona["id"])

            chosen = with_preference["explanation"]["preset"]
            self.assertEqual(chosen["name"], "Avery portrait")
            self.assertEqual(chosen["source"], "persona_preference")
            self.assertIn("preferred recipe", chosen["reason"])

    def test_the_task_model_choice_still_outranks_a_standing_preference(self):
        provider = FakeChatProvider(
            ["Here."], task_outputs={CAPABILITY_PLANNING: {"requests": [{**PLANNED, "preset": "preset_1"}]}}
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            self._ready(running)
            persona = self._persona(running)
            preset = self._second_preset(running)
            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"preferred_preset_ids": [preset["id"]]},
            )

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
            chosen = requests[0]["media_plan"]["explanation"]["preset"]
            # preset_1 is the highest-priority preset, not the persona's pick.
            self.assertEqual(chosen["source"], "task_model")

    def test_a_preference_naming_an_unusable_preset_is_ignored_rather_than_blocking(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"preferred_preset_ids": ["does-not-exist"]},
            )
            preview = self._preview(running, persona["id"])

            # A stale preference must not stop a picture being made.
            self.assertEqual(preview["status"], "ready", preview)
            self.assertEqual(preview["explanation"]["preset"]["source"], "deterministic")

    def test_a_preference_list_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            persona = self._persona(running)
            refused = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={"preferred_preset_ids": [f"preset-{index}" for index in range(20)]},
            )
            self.assertEqual(refused.status_code, 422, refused.text)


if __name__ == "__main__":
    unittest.main()
