"""Executable coverage for documented security claims that had none.

`docs/security-model.md` states these as required controls. Each held when audited,
but nothing failed if it stopped holding. These tests assert the absence of things,
which is the direction that silently regresses.
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.resource_coordination import ResourceRequest
from tests.support import TestApp
from tests import test_resource_coordination as coordination


class ResourceAuditContentTests(unittest.TestCase):
    """ "Resource audit rows omit provider URLs, credentials, prompts, outputs, and
    model-generated content." The audit writer only strips null values, so the guarantee
    rests entirely on what every caller happens to pass."""

    def test_audit_rows_carry_no_endpoint_url_credential_or_generated_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = coordination.provider_set(comfy_free=0, comfy_release=4096)
            with TestApp(Path(tmp), resource_providers=resources) as app:
                user_id = app.create_and_login()
                policy = app.client.put(
                    "/api/v1/admin/resource-coordination",
                    json={
                        "mode": "managed",
                        "reserve_vram_mb": 0,
                        "max_wait_seconds": 3,
                        "poll_interval_seconds": 0.25,
                        "authorizations": [
                            {"provider": name, "exclusive_control": True, "allow_release": True}
                            for name in ("ollama", "comfyui", "automatic1111")
                        ],
                    },
                )
                self.assertEqual(policy.status_code, 200, policy.text)
                request = ResourceRequest(user_id, "comfyui", app.config.comfyui_base_url, "secret-api-token", 1000)
                job_id = coordination.ResourceCoordinationTests._submit(app, user_id, request)
                self.assertEqual(app.services.jobs.wait(user_id, job_id, timeout=4)["status"], "completed")

                events = app.services.resource_coordination.events()
                serialized = json.dumps(events, default=str)

                self.assertTrue(events, "expected at least one audit row")
                # The endpoint fingerprint is deliberately retained; the address is not.
                self.assertNotIn(app.config.comfyui_base_url, serialized)
                self.assertNotIn("secret-api-token", serialized)
                for forbidden in ("http://", "https://", "password", "api_key", "prompt"):
                    self.assertNotIn(forbidden, serialized.lower(), f"audit row leaked {forbidden!r}")


class PersonaMaterialIsolationTests(unittest.TestCase):
    """Authored persona material is not conversation. If it reached the memory extractor
    it could be learned back as a fact about the user, and if it reached the summarizer it
    would be compressed into durable history that nobody said."""

    def _persona_with_material(self, running):
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = running.client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
        card = running.client.put(
            f"/api/v1/personas/{persona['id']}/card",
            json={
                "card_definition": "Runs a bindery called Quillon Works.",
                "card_example_dialogue": "<START>\n{{user}}: You up?\n{{char}}: Barely, Quillon paperwork.\n",
            },
        )
        assert card.status_code == 200, card.text
        lore = running.client.post(
            f"/api/v1/personas/{persona['id']}/lore",
            json={
                "title": "Sister",
                "content": "Her sister Perrivale is a nurse.",
                "keys": ["sister"],
                "secondary_keys": [],
                "always_on": False,
                "case_sensitive": False,
                "priority": 50,
                "enabled": True,
            },
        )
        assert lore.status_code == 200, lore.text
        return workspace, persona

    def test_card_lore_and_example_dialogue_never_reach_platform_task_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                running.create_and_login()
                workspace, persona = self._persona_with_material(running)
                chat = running.client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = running.client.post(
                    f"/api/v1/chats/{chat['id']}/turns",
                    json={"text": "How is your sister?"},
                )
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(running.wait_job(started.json()["job"]["id"])["status"], "completed")

                persona_prompt = running.chat_provider.requests[0].messages[0]["content"]
                self.assertIn("Quillon Works", persona_prompt)
                self.assertIn("Perrivale", persona_prompt)

                # Distinctive strings so a match cannot be coincidental.
                task_text = json.dumps(
                    [request.messages for request in running.chat_provider.task_requests],
                    default=str,
                )
                for authored in ("Quillon", "Perrivale"):
                    self.assertNotIn(authored, task_text, f"{authored} reached a platform task role")

    def test_authored_material_is_not_written_into_the_durable_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                running.create_and_login()
                workspace, persona = self._persona_with_material(running)
                chat = running.client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "How is your sister?"})
                running.wait_job(started.json()["job"]["id"])

                messages = json.dumps(running.client.get(f"/api/v1/chats/{chat['id']}").json())
                for authored in ("Quillon", "Perrivale"):
                    self.assertNotIn(authored, messages, f"{authored} was persisted as conversation")


if __name__ == "__main__":
    unittest.main()
