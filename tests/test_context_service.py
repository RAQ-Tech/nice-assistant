import tempfile
import threading
import time
import unittest
from pathlib import Path

from sqlalchemy import func, select

from app.models import Memory
from app.provider_contracts import ChatDelta, ProviderError
from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


class CausalProvider(FakeChatProvider):
    def __init__(self):
        super().__init__()
        self.first_release = threading.Event()
        self._lock = threading.Lock()

    def stream(self, request, cancellation):
        with self._lock:
            self.requests.append(request)
            request_number = len(self.requests)
        self.started.set()
        current = request.messages[-1]["content"]
        if request_number == 1:
            while not self.first_release.wait(0.01):
                cancellation.raise_if_cancelled()
            reply = "first assistant reply"
        else:
            reply = f"reply to {current}"
        yield ChatDelta(reply, done=True, metadata={"prompt_eval_count": 123})


class SummaryFailureProvider(FakeChatProvider):
    def generate(self, request, cancellation):
        if self._task_role(request) == "conversation_summary":
            self.task_requests.append(request)
            raise ProviderError(
                provider="ollama",
                code="summary_unavailable",
                user_message="Summary unavailable.",
                retryable=True,
            )
        return super().generate(request, cancellation)

    def stream(self, request, cancellation):
        self.requests.append(request)
        yield ChatDelta("main reply", done=True)


class ContextServiceTests(unittest.TestCase):
    def test_browser_exposes_truthful_saved_memory_and_context_controls(self):
        source_root = Path(__file__).resolve().parents[1] / "frontend" / "src"
        source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.ts"))
        self.assertNotIn("memory_auto_save_user_facts", source)
        self.assertIn("Use saved memory", source)
        self.assertIn("Chat details", source)
        self.assertIn("A proposed memory that does not enter prompts until you approve it", source)
        self.assertIn("Only approved active memories enter prompts", source)
        self.assertIn("Default memory mode", source)
        self.assertIn("Approve", source)
        self.assertIn("Undo", source)
        self.assertIn("Context window tokens", source)
        self.assertIn("context_window_tokens", source)
        self.assertIn("/api/v1", source)

    def test_context_diagnostics_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login("owner")
            chat = running.client.post("/api/v1/chats", json={"title": "Private"}).json()
            running.client.delete("/api/v1/session")
            running.create_and_login("other")
            self.assertEqual(
                running.client.get(f"/api/v1/chats/{chat['id']}/context").status_code,
                404,
            )

    def test_same_chat_turns_are_causal_with_multiple_workers(self):
        provider = CausalProvider()
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), chat_provider=provider, interactive_workers=2) as running,
        ):
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Causal"}).json()
            first = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "first user turn"}).json()
            self.assertTrue(provider.started.wait(2))
            second = running.client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "second user turn"}).json()
            time.sleep(0.1)
            self.assertEqual(len(provider.requests), 1)
            provider.first_release.set()
            self.assertEqual(running.wait_job(first["job"]["id"])["status"], "completed")
            self.assertEqual(running.wait_job(second["job"]["id"])["status"], "completed")
            second_prompt = provider.requests[1].messages
            self.assertIn(
                {"role": "assistant", "content": "first assistant reply"},
                second_prompt,
            )
            self.assertEqual(
                [message for message in second_prompt if message["content"] == "second user turn"],
                [{"role": "user", "content": "second user turn"}],
            )

    def test_context_window_is_sent_and_accounted(self):
        provider = CausalProvider()
        provider.first_release.set()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Budget"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "budget this turn",
                    "model_settings": {"context_window_tokens": 2048, "num_predict": 256},
                },
            ).json()
            self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")
            self.assertEqual(provider.requests[-1].options["num_ctx"], 2048)
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["context"]["context_window_tokens"], 2048)
            self.assertEqual(turn["context"]["prompt_tokens_actual"], 123)
            self.assertLessEqual(
                turn["context"]["prompt_tokens_estimated"],
                turn["context"]["prompt_budget_tokens"],
            )
            detail = running.client.get(f"/api/v1/chats/{chat['id']}/context")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["latest_turn_context"]["turn_id"], started["turn"]["id"])
            clamped = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "clamp to the provider maximum",
                    "model_settings": {"context_window_tokens": 16384, "num_predict": 256},
                },
            ).json()
            self.assertEqual(running.wait_job(clamped["job"]["id"])["status"], "completed")
            self.assertEqual(provider.requests[-1].options["num_ctx"], 8192)

    def test_successful_turn_never_silently_creates_memory_and_deduplicates_saved_memory(self):
        provider = FakeChatProvider(["remembered"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Memory"}).json()
            response = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "Favorite   color is Blue"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            duplicate = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "favorite color is blue"},
            )
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "My name is Taylor", "memory_mode": "saved"},
            ).json()
            completed = running.wait_job(started["job"]["id"])
            running.wait_job(completed["result"]["memory_extraction_job_id"])
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                count = uow.session.scalar(select(func.count()).select_from(Memory))
            self.assertEqual(count, 1)
            system = provider.requests[-1].messages[0]["content"].casefold()
            self.assertEqual(system.count("favorite color is blue"), 1)
            self.assertNotIn("my name is taylor", system)

    def test_long_chat_creates_and_reuses_durable_summary(self):
        provider = FakeChatProvider(["compact reply"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Long"}).json()
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                for index in range(14):
                    role = "user" if index % 2 == 0 else "assistant"
                    uow.repo.add_message(chat["id"], role, f"old-{index} " + ("context " * 80))
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "continue the long conversation",
                    "model_settings": {"context_window_tokens": 2048, "num_predict": 256},
                },
            ).json()
            self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")
            context = running.client.get(f"/api/v1/chats/{chat['id']}/context").json()
            self.assertIsNotNone(context["summary"])
            self.assertEqual(context["summary"]["prompt_version"], "conversation-summary-task-v2")
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["context"]["summary_id"], context["summary"]["id"])
            self.assertGreater(turn["context"]["omitted_message_count"], 0)
            self.assertGreaterEqual(len(provider.requests), 1)
            self.assertGreaterEqual(len(provider.task_requests), 1)

    def test_summary_failure_degrades_without_failing_main_turn(self):
        provider = SummaryFailureProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Fallback"}).json()
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                for index in range(14):
                    uow.repo.add_message(
                        chat["id"],
                        "user" if index % 2 == 0 else "assistant",
                        f"legacy-{index} " + ("large " * 100),
                    )
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "still answer me",
                    "model_settings": {"context_window_tokens": 2048, "num_predict": 256},
                },
            ).json()
            job = running.wait_job(started["job"]["id"])
            self.assertEqual(job["status"], "completed")
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["context"]["degraded_reason"], "summary_provider_failed")

    def test_two_hundred_turn_transcript_stays_inside_budget(self):
        provider = FakeChatProvider(["bounded reply"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Two hundred turns"}).json()
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                for turn_number in range(200):
                    uow.repo.add_message(chat["id"], "user", f"user turn {turn_number} " + ("detail " * 8))
                    uow.repo.add_message(
                        chat["id"],
                        "assistant",
                        f"assistant turn {turn_number} " + ("response " * 8),
                    )
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "current request appears once",
                    "model_settings": {"context_window_tokens": 2048, "num_predict": 256},
                },
            ).json()
            self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            accounting = turn["context"]
            self.assertLessEqual(accounting["prompt_tokens_estimated"], accounting["prompt_budget_tokens"])
            self.assertGreater(accounting["omitted_message_count"], 300)
            main_prompt = provider.requests[-1].messages
            self.assertEqual(
                [message for message in main_prompt if message["content"] == "current request appears once"],
                [{"role": "user", "content": "current request appears once"}],
            )

    def test_oversized_current_request_fails_without_assistant_message(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Too large"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={
                    "text": "x" * 20_000,
                    "model_settings": {"context_window_tokens": 2048, "num_predict": 256},
                },
            ).json()
            job = running.wait_job(started["job"]["id"])
            self.assertEqual(job["status"], "failed")
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["error"]["code"], "context_too_large")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual([message["role"] for message in detail["messages"]], ["user"])


if __name__ == "__main__":
    unittest.main()
