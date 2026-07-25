import tempfile
import unittest
from pathlib import Path

from tests.support import FakeChatProvider, TestApp


class HumanExperienceScenarioTests(unittest.TestCase):
    def test_changing_personas_requires_a_new_chat_and_does_not_copy_prior_history(self):
        provider = FakeChatProvider(
            [
                "I am responding as the original persona.",
                "I am responding as the new persona.",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            first = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "Avery",
                    "system_prompt": "Use the private persona marker ORCHID-VOICE.",
                },
            ).json()
            second = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "Robin",
                    "system_prompt": "Use the private persona marker CEDAR-VOICE.",
                },
            ).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "persona_id": first["id"],
                    "access_context": {"kind": "workspace", "workspace_id": workspace["id"]},
                    "title": "Persona handoff",
                    "memory_mode": "off",
                },
            ).json()

            first_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Say hello with the private phrase ALPINE-FIRST.", "memory_mode": "off"},
            ).json()
            self.assertEqual(running.wait_job(first_turn["job"]["id"])["status"], "completed")
            first_prompt = "\n".join(message["content"] for message in provider.requests[-1].messages)
            self.assertIn("ORCHID-VOICE", first_prompt)
            self.assertNotIn("CEDAR-VOICE", first_prompt)

            updated = running.client.put(
                f"/api/v1/chats/{chat['id']}",
                json={"persona_id": second["id"]},
            )
            self.assertEqual(updated.status_code, 409, updated.text)
            unchanged = running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]
            self.assertEqual(unchanged["binding"]["persona_id"], first["id"])

            next_chat = running.client.post(
                "/api/v1/chats",
                json={
                    "persona_id": second["id"],
                    "access_context": {"kind": "workspace", "workspace_id": workspace["id"]},
                    "title": "New persona chat",
                    "memory_mode": "off",
                },
            ).json()
            second_turn = running.client.post(
                f"/api/v1/chats/{next_chat['id']}/turns",
                json={"text": "Say hello again.", "memory_mode": "off"},
            ).json()
            self.assertEqual(running.wait_job(second_turn["job"]["id"])["status"], "completed")
            second_prompt = "\n".join(message["content"] for message in provider.requests[-1].messages)
            self.assertIn("CEDAR-VOICE", second_prompt)
            self.assertNotIn("ORCHID-VOICE", second_prompt)
            self.assertNotIn("ALPINE-FIRST", second_prompt)

    def test_a_pending_correction_cannot_silently_replace_approved_memory(self):
        provider = FakeChatProvider(
            [
                "I heard the correction.",
                "I will use only reviewed memory.",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            _workspace, persona = running.ensure_bound_persona()
            active = running.client.post(
                "/api/v1/memories",
                json={
                    "content": "The user's favorite color is blue.",
                    "grants": [
                        {
                            "grant_type": "persona",
                            "target_id": persona["id"],
                        }
                    ],
                },
            ).json()
            chat = running.create_chat(
                {"title": "Correction boundary", "memory_mode": "saved"},
                persona_id=persona["id"],
                context={"kind": "personal"},
            )
            correction_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "Correction: my favorite color is green.",
                    "memory_mode": "off",
                },
            ).json()
            self.assertEqual(
                running.wait_job(correction_turn["job"]["id"])["status"],
                "completed",
            )
            proposed = running.client.post(
                "/api/v1/memory-proposals",
                json={
                    "content": "Correction: the user's favorite color is green.",
                    "source_message_id": correction_turn["turn"]["user_message_id"],
                },
            ).json()
            self.assertEqual(active["status"], "active")
            self.assertEqual(proposed["status"], "pending")
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What is my favorite color?", "memory_mode": "saved"},
            ).json()
            self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")

            system_text = "\n".join(
                message["content"] for message in provider.requests[-1].messages if message["role"] == "system"
            )
            self.assertIn("favorite color is blue", system_text.casefold())
            self.assertNotIn("favorite color is green", system_text.casefold())


if __name__ == "__main__":
    unittest.main()
