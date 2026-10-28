"""Trying again, answering again, correcting, and deleting an exchange.

A failed reply could not be retried and a sent message could not be taken
back: the only way out of a bad turn was a new chat. These are the four things
a messaging app is expected to let you do with what you already said.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest

from app.provider_contracts import ProviderError
from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


def _messages(running: TestApp, chat_id: str) -> list[tuple[str, str]]:
    return [(item["role"], item["text"]) for item in running.client.get(f"/api/v1/chats/{chat_id}").json()["messages"]]


def _say(running: TestApp, chat_id: str, text: str) -> dict:
    started = running.client.post(f"/api/v1/chats/{chat_id}/turns", json={"text": text, "memory_mode": "off"}).json()
    running.wait_job(started["job"]["id"])
    return started


class AnsweringAgainTests(unittest.TestCase):
    def test_a_failed_reply_can_be_tried_again_without_saying_it_twice(self):
        provider = FakeChatProvider(
            ["Here I am."], error=ProviderError(provider="ollama", code="unavailable", user_message="down")
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            failed = _say(running, chat["id"], "Are you there?")
            self.assertEqual(running.client.get(f"/api/v1/turns/{failed['turn']['id']}").json()["status"], "failed")

            provider.error = None
            again = running.client.post(
                f"/api/v1/chats/{chat['id']}/messages/{failed['turn']['user_message_id']}/again", json={}
            )
            self.assertEqual(again.status_code, 202, again.text)
            self.assertEqual(running.wait_job(again.json()["job"]["id"])["status"], "completed")

            self.assertEqual(_messages(running, chat["id"]), [("user", "Are you there?"), ("assistant", "Here I am.")])
            self.assertEqual(running.client.get(f"/api/v1/turns/{failed['turn']['id']}").status_code, 404)

    def test_a_reply_can_be_answered_again(self):
        provider = FakeChatProvider(["First answer."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            _say(running, chat["id"], "Tell me something.")
            reply_id = running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"][-1]["id"]

            provider.chunks = ["Second answer."]
            again = running.client.post(f"/api/v1/chats/{chat['id']}/messages/{reply_id}/again", json={})
            self.assertEqual(running.wait_job(again.json()["job"]["id"])["status"], "completed")

            self.assertEqual(
                _messages(running, chat["id"]), [("user", "Tell me something."), ("assistant", "Second answer.")]
            )

    def test_a_message_can_be_corrected_and_only_the_correction_is_answered(self):
        provider = FakeChatProvider(["Noted."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            said = _say(running, chat["id"], "My sister is called Anna.")

            again = running.client.post(
                f"/api/v1/chats/{chat['id']}/messages/{said['turn']['user_message_id']}/again",
                json={"text": "My sister is called Hannah."},
            )
            self.assertEqual(running.wait_job(again.json()["job"]["id"])["status"], "completed")

            self.assertEqual(_messages(running, chat["id"])[0], ("user", "My sister is called Hannah."))
            prompt = "\n".join(message["content"] for message in provider.requests[-1].messages)
            self.assertIn("Hannah", prompt)
            self.assertNotIn("Anna.", prompt)

    def test_only_the_latest_message_can_be_answered_again(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            first = _say(running, chat["id"], "One.")
            _say(running, chat["id"], "Two.")
            before = _messages(running, chat["id"])

            response = running.client.post(
                f"/api/v1/chats/{chat['id']}/messages/{first['turn']['user_message_id']}/again", json={}
            )

            # Every reply after it would be answering something no longer said.
            self.assertEqual(response.status_code, 409)
            self.assertEqual(_messages(running, chat["id"]), before)

    def test_nothing_is_answered_again_while_the_reply_is_still_being_written(self):
        gate = threading.Event()
        provider = FakeChatProvider(["Slowly."], gate=gate)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            started = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hello?"}).json()
            try:
                self.assertTrue(provider.started.wait(5))
                response = running.client.post(
                    f"/api/v1/chats/{chat['id']}/messages/{started['turn']['user_message_id']}/again", json={}
                )
                self.assertEqual(response.status_code, 409)
            finally:
                gate.set()
            running.wait_job(started["job"]["id"])

    def test_work_still_owed_to_the_old_reply_is_cancelled(self):
        gate = threading.Event()
        provider = FakeChatProvider(["Noted."], memory_gate=gate)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns", json={"text": "I live in Leeds.", "memory_mode": "saved"}
            ).json()
            try:
                self.assertTrue(provider.memory_started.wait(5))
                job = running.client.get(f"/api/v1/jobs/{started['job']['id']}").json()
                extraction = job["result"]["memory_extraction_job_id"]
                again = running.client.post(
                    f"/api/v1/chats/{chat['id']}/messages/{started['turn']['user_message_id']}/again",
                    json={"text": "I live in York.", "memory_mode": "off"},
                )
                self.assertEqual(again.status_code, 202, again.text)
                # Memories about a message that no longer exists would be
                # proposed about something nobody said.
                self.assertEqual(running.wait_job(extraction)["status"], "cancelled")
            finally:
                gate.set()
            self.assertEqual(running.wait_job(again.json()["job"]["id"])["status"], "completed")


class DeletingAnExchangeTests(unittest.TestCase):
    def test_a_message_and_its_reply_go_together_and_are_forgotten(self):
        provider = FakeChatProvider(["Reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            first = _say(running, chat["id"], "Something I regret saying.")
            _say(running, chat["id"], "Something else.")

            response = running.client.delete(f"/api/v1/chats/{chat['id']}/messages/{first['turn']['user_message_id']}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(_messages(running, chat["id"]), [("user", "Something else."), ("assistant", "Reply.")])

            _say(running, chat["id"], "And now?")
            prompt = "\n".join(message["content"] for message in provider.requests[-1].messages)
            self.assertNotIn("regret", prompt)

    def test_a_summary_that_remembers_it_is_forgotten_too(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            first = _say(running, chat["id"], "A secret.")
            _say(running, chat["id"], "Ordinary.")
            factory, secrets = running.services.runtime.session_factory, running.services.runtime.secret_store
            with UnitOfWork(factory, secrets) as uow:
                last = uow.repo.messages(chat["id"])[-1]
                uow.repo.add_summary(
                    user_id=user_id,
                    chat_id=chat["id"],
                    previous_summary_id=None,
                    through_message_id=last.id,
                    provider="test",
                    model="test",
                    prompt_version="test",
                    source_digest="test",
                    source_message_count=4,
                    content="They told a secret.",
                    estimated_tokens=6,
                )

            running.client.delete(f"/api/v1/chats/{chat['id']}/messages/{first['turn']['user_message_id']}")

            # The summary covered the deleted exchange; keeping it would keep
            # the secret. Compaction writes a new one from what is left.
            self.assertIsNone(running.client.get(f"/api/v1/chats/{chat['id']}/context").json()["summary"])

    def test_another_persons_chat_is_not_found(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login("owner")
            chat = running.client.post("/api/v1/chats", json={}).json()
            said = _say(running, chat["id"], "Mine.")
            running.client.delete("/api/v1/session")
            running.create_and_login("guest")

            response = running.client.delete(f"/api/v1/chats/{chat['id']}/messages/{said['turn']['user_message_id']}")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
