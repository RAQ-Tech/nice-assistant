"""The persona speaks first in a new conversation.

A first conversation used to open on "Say hello to Nova." and wait. Now the
persona greets the owner in its own words, through the same turn every reply
takes - streamed, spoken, kept - but with nothing said in front of it, so no
instruction is stored that a later prompt could replay as somebody's words.
See ADR 0052.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import ProviderError
from app.turn_pipeline import OPENING_INSTRUCTION
from tests.support import FakeChatProvider, TestApp


GREETING = "Oh, hello! I'm Nova. I've been looking forward to this."


def _messages(running: TestApp, chat_id: str) -> list[tuple[str, str]]:
    return [(item["role"], item["text"]) for item in running.client.get(f"/api/v1/chats/{chat_id}").json()["messages"]]


def _companion(running: TestApp) -> dict:
    workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
    persona = running.client.post(
        "/api/v1/personas",
        json={"workspace_id": workspace["id"], "name": "Nova", "system_prompt": "Warm and curious."},
    ).json()
    return running.client.post(
        "/api/v1/chats", json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"}
    ).json()


def _greet(running: TestApp, chat_id: str) -> dict:
    started = running.client.post(f"/api/v1/chats/{chat_id}/greeting", json={"memory_mode": "saved"})
    assert started.status_code == 202, started.text
    running.wait_job(started.json()["job"]["id"])
    return started.json()


class GreetingTests(unittest.TestCase):
    def test_the_persona_opens_the_conversation_in_its_own_words(self):
        provider = FakeChatProvider([GREETING])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = _companion(running)

            started = _greet(running, chat["id"])

            # Nothing anybody said, and nothing stored but the greeting itself.
            self.assertIsNone(started["turn"]["user_message_id"])
            self.assertEqual(_messages(running, chat["id"]), [("assistant", GREETING)])
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["status"], "completed")
            self.assertEqual(turn["accumulated_text"], GREETING)
            # The persona is who speaks: its own instructions are in the prompt,
            # and the application's request comes last, as the application.
            prompt = provider.requests[-1].messages
            self.assertIn("Nova", prompt[0]["content"])
            self.assertEqual(prompt[-1], {"role": "system", "content": OPENING_INSTRUCTION})
            self.assertFalse([message for message in prompt if message["role"] == "user"])

    def test_a_greeting_names_nothing_learns_nothing_and_plans_no_picture(self):
        provider = FakeChatProvider([GREETING], memory_candidates=[{"content": "Likes tea", "scope": "global"}])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = _companion(running)

            started = _greet(running, chat["id"])

            result = running.client.get(f"/api/v1/jobs/{started['job']['id']}").json()["result"]
            self.assertFalse(result.get("followup_job_ids"))
            self.assertIsNone(result.get("memory_extraction_job_id"))
            self.assertEqual(provider.task_requests, [])
            self.assertEqual(running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"], "New chat")

    def test_the_greeting_is_remembered_as_said_and_its_request_is_never_replayed(self):
        provider = FakeChatProvider([GREETING])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = _companion(running)
            _greet(running, chat["id"])

            provider.chunks = ["Lovely to meet you too."]
            said = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hi Nova, I'm Sam.", "memory_mode": "off"}
            ).json()
            running.wait_job(said["job"]["id"])

            prompt = provider.requests[-1].messages
            self.assertEqual(
                [(item["role"], item["content"]) for item in prompt[1:]],
                [("assistant", GREETING), ("user", "Hi Nova, I'm Sam.")],
            )
            self.assertFalse(any(OPENING_INSTRUCTION in item["content"] for item in prompt))
            # The first thing the owner said still names the conversation.
            self.assertNotEqual(running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"], "New chat")

    def test_only_a_conversation_where_nothing_has_been_said_can_be_opened(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            chat = _companion(running)
            said = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hello?"}).json()
            running.wait_job(said["job"]["id"])
            before = _messages(running, chat["id"])

            refused = running.client.post(f"/api/v1/chats/{chat['id']}/greeting", json={})

            self.assertEqual(refused.status_code, 409)
            self.assertEqual(_messages(running, chat["id"]), before)

    def test_a_greeting_that_fails_leaves_the_conversation_as_it_was(self):
        provider = FakeChatProvider(
            [GREETING], error=ProviderError(provider="ollama", code="unavailable", user_message="down")
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = _companion(running)

            started = _greet(running, chat["id"])

            self.assertEqual(running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()["status"], "failed")
            self.assertEqual(_messages(running, chat["id"]), [])
            # The conversation carries on from nothing, as if no greeting was tried.
            provider.error = None
            provider.chunks = ["Here I am."]
            said = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hello?"}).json()
            self.assertEqual(running.wait_job(said["job"]["id"])["status"], "completed")
            self.assertEqual(_messages(running, chat["id"]), [("user", "Hello?"), ("assistant", "Here I am.")])

    def test_the_greeting_can_be_said_again_and_deleted(self):
        provider = FakeChatProvider([GREETING])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = _companion(running)
            first = _greet(running, chat["id"])
            greeting_id = running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"][0]["id"]

            corrected = running.client.post(
                f"/api/v1/chats/{chat['id']}/messages/{greeting_id}/again", json={"text": "Something else"}
            )
            self.assertEqual(corrected.status_code, 400)

            provider.chunks = ["Hi there - I'm Nova."]
            again = running.client.post(f"/api/v1/chats/{chat['id']}/messages/{greeting_id}/again", json={})
            self.assertEqual(again.status_code, 202, again.text)
            self.assertIsNone(again.json()["turn"]["user_message_id"])
            self.assertEqual(running.wait_job(again.json()["job"]["id"])["status"], "completed")
            self.assertEqual(_messages(running, chat["id"]), [("assistant", "Hi there - I'm Nova.")])
            self.assertEqual(running.client.get(f"/api/v1/turns/{first['turn']['id']}").status_code, 404)

            newest = running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"][0]["id"]
            deleted = running.client.delete(f"/api/v1/chats/{chat['id']}/messages/{newest}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(_messages(running, chat["id"]), [])
            self.assertEqual(running.client.get(f"/api/v1/turns/{again.json()['turn']['id']}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
