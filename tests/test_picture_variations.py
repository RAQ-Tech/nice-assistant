"""Steering pictures from the chat: another take, or a different look.

Owner mandate 2026-08-27: changing what pictures look like must stop meaning
a trip to settings. These pin the two buttons' honesty: "again" runs the
same recipe pinned - a button called another take that silently switched
recipes would be a lie - and "different_look" sets that recipe aside so
routing must choose another, refusing plainly when there is nothing else to
choose.
"""

from pathlib import Path
import tempfile
import unittest

from tests.support import TestApp
from tests.test_capabilities import CAPABILITY_PLANNING, FakeChatProvider, FakeImageProvider

PLANNED = {
    "capability_key": "media.generate_image",
    "scene": {
        "subject": "a moonlit garden",
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


class PictureVariationTests(unittest.TestCase):
    def _completed_picture(self, running):
        """Drive one task-planned picture to completion; return (chat, request)."""

        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local/automatic1111"}},
        )
        chat = running.client.post("/api/v1/chats", json={"memory_mode": "off"}).json()
        accepted = running.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Show me a moonlit garden", "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        running.wait_job(chat_job["result"]["followup_job_id"])
        request = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][0]
        running.wait_job(request["job_id"])
        completed = running.client.get(f"/api/v1/capability-requests/{request['id']}").json()
        self.assertEqual(completed["status"], "completed")
        return chat, completed

    def _preset_of(self, running, request_id):
        detail = running.client.get(f"/api/v1/capability-requests/{request_id}").json()
        preset = (detail.get("media_plan") or {}).get("explanation", {}).get("preset") or {}
        return preset.get("id")

    def test_another_take_reruns_the_same_recipe(self):
        provider = FakeChatProvider(["I'll make that."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            chat, completed = self._completed_picture(running)
            original_preset = self._preset_of(running, completed["id"])
            self.assertTrue(original_preset)

            varied = running.client.post(
                f"/api/v1/capability-requests/{completed['id']}/variations",
                json={"mode": "again"},
            )
            self.assertEqual(varied.status_code, 200, varied.text)
            body = varied.json()
            running.wait_job(body["job_id"])
            second = running.client.get(f"/api/v1/capability-requests/{body['id']}").json()

            self.assertEqual(second["status"], "completed")
            # The same recipe made both pictures - that is what the button said.
            self.assertEqual(self._preset_of(running, body["id"]), original_preset)
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            attached = [a for m in detail["messages"] for a in m.get("attachments", [])]
            self.assertEqual(len(attached), 2)

    def test_a_different_look_refuses_when_there_is_no_other_recipe(self):
        provider = FakeChatProvider(["I'll make that."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            _chat, completed = self._completed_picture(running)

            varied = running.client.post(
                f"/api/v1/capability-requests/{completed['id']}/variations",
                json={"mode": "different_look"},
            )
            self.assertEqual(varied.status_code, 200, varied.text)
            body = varied.json()

            # One enabled recipe, set aside: the honest answer is a plain
            # refusal naming the way out, not the same look wearing a new
            # button.
            self.assertEqual(body["status"], "failed")
            self.assertIn("Add another model", body["error"]["message"])

    def test_a_different_look_uses_another_recipe_when_one_exists(self):
        provider = FakeChatProvider(["I'll make that."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            chat, completed = self._completed_picture(running)
            original_preset = self._preset_of(running, completed["id"])
            added = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "image",
                    "name": "Second Look",
                    "provider_key": "local-image",
                    "backend": "automatic1111",
                    "external_id": "second-look.safetensors",
                    "enabled": True,
                    "priority": 40,
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
            )
            self.assertEqual(added.status_code, 201, added.text)
            # The lazy pass gives the new model its recipe.
            running.client.get("/api/v1/media-catalog")

            varied = running.client.post(
                f"/api/v1/capability-requests/{completed['id']}/variations",
                json={"mode": "different_look"},
            )
            self.assertEqual(varied.status_code, 200, varied.text)
            body = varied.json()
            running.wait_job(body["job_id"])
            second = running.client.get(f"/api/v1/capability-requests/{body['id']}").json()

            self.assertEqual(second["status"], "completed", second)
            different_preset = self._preset_of(running, body["id"])
            self.assertTrue(different_preset)
            self.assertNotEqual(different_preset, original_preset)
            _ = chat

    def test_only_finished_pictures_can_be_varied(self):
        provider = FakeChatProvider(["I'll make that."], task_outputs={CAPABILITY_PLANNING: {"requests": [PLANNED]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            _chat, completed = self._completed_picture(running)
            varied = running.client.post(
                f"/api/v1/capability-requests/{completed['id']}/variations",
                json={"mode": "again"},
            ).json()
            running.wait_job(varied["job_id"])

            # The variation itself completed, so IT can be varied - but a
            # request that never finished cannot.
            pending_like = running.client.post(
                f"/api/v1/capability-requests/{varied['id']}/variations",
                json={"mode": "again"},
            )
            self.assertEqual(pending_like.status_code, 200)
            missing = running.client.post(
                "/api/v1/capability-requests/not-a-real-id/variations",
                json={"mode": "again"},
            )
            self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
