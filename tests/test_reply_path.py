"""Nothing waits in front of the reply. See ADR 0043.

Each test here pins one thing that used to sit between somebody finishing a
sentence and the persona starting to answer, and shows it no longer does.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from app.observability import MetricsRegistry, RedactedJsonFormatter
from app.provider_contracts import ChatDelta, LoadedModel, provider_endpoint, shared_context_window
from app.repositories import UnitOfWork
from app.task_contracts import CONVERSATION_SUMMARY
from tests.support import FakeChatProvider, TestApp


class BlockingWorkStaysOffTheEventLoopTests(unittest.TestCase):
    def test_a_transcription_in_progress_does_not_hold_other_requests(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            entered = threading.Event()
            release = threading.Event()
            responses = {}

            def slow_transcribe(_user_id, _filename, _content):
                entered.set()
                release.wait(5)
                return {"text": "done", "language": None}

            def send():
                responses["stt"] = running.client.post(
                    "/api/v1/speech/transcriptions",
                    files={"file": ("turn.webm", b"webm bytes", "audio/webm")},
                )

            # If the conversion ran on the event loop, the health check below
            # would wait for this timer rather than answer at once.
            safety = threading.Timer(3, release.set)
            with mock.patch.object(running.services.speech, "transcribe", side_effect=slow_transcribe):
                sender = threading.Thread(target=send)
                sender.start()
                safety.start()
                try:
                    self.assertTrue(entered.wait(5))
                    started = time.monotonic()
                    health = running.client.get("/health")
                    elapsed = time.monotonic() - started
                    still_transcribing = not release.is_set()
                finally:
                    release.set()
                    safety.cancel()
                    sender.join(5)

            self.assertEqual(health.status_code, 200)
            self.assertTrue(still_transcribing)
            self.assertLess(elapsed, 1.5)
            self.assertEqual(responses["stt"].json()["text"], "done")

    def test_a_stuck_audio_conversion_gives_up_rather_than_waiting_forever(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"stt_provider": "local", "preferences": {"stt_local_base_url": "http://127.0.0.1:8000"}},
            )
            seen = {}

            def stuck(command, **kwargs):
                seen["timeout"] = kwargs.get("timeout")
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 0)

            with mock.patch("app.speech_service.subprocess.run", side_effect=stuck):
                response = running.client.post(
                    "/api/v1/speech/transcriptions",
                    files={"file": ("turn.webm", b"webm bytes", "audio/webm")},
                )

            self.assertIsNotNone(seen["timeout"])
            self.assertEqual(response.status_code, 500)
            self.assertIn("Audio conversion failed", response.text)


def _long_transcript(running: TestApp, chat_id: str, count: int = 14) -> None:
    with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
        for index in range(count):
            uow.repo.add_message(chat_id, "user" if index % 2 == 0 else "assistant", f"old-{index} " + ("words " * 80))


SMALL_WINDOW = {"context_window_tokens": 2048, "num_predict": 256}


def _reply_job(running: TestApp, job_id: str, timeout: float = 10) -> dict:
    """The reply's own job, without waiting for its follow-ups as `wait_job` does."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = running.client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"reply did not finish: {job}")


class SummaryWaitsForTheReplyTests(unittest.TestCase):
    def test_a_reply_does_not_wait_for_the_summary_model(self):
        gate = threading.Event()
        provider = FakeChatProvider(["here I am"], task_gates={CONVERSATION_SUMMARY: gate})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Long"}).json()
            _long_transcript(running, chat["id"])
            try:
                started = running.client.post(
                    f"/api/v1/chats/{chat['id']}/turns",
                    json={"text": "still there?", "memory_mode": "off", "model_settings": SMALL_WINDOW},
                ).json()
                # The summary model is held shut, and the reply arrives anyway.
                completed = _reply_job(running, started["job"]["id"])
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(completed["result"]["text"], "here I am")
                self.assertTrue(provider.task_started[CONVERSATION_SUMMARY].wait(5))
            finally:
                gate.set()
            self.assertEqual(running.wait_job(completed["result"]["summary_job_id"])["status"], "completed")

    def test_a_short_chat_schedules_no_summary_at_all(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Short"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns", json={"text": "hello", "memory_mode": "off"}
            ).json()
            completed = running.wait_job(started["job"]["id"])
            self.assertNotIn("summary_job_id", completed["result"])


class ContextLoadingStopsAtTheSummaryTests(unittest.TestCase):
    def test_reading_from_the_summary_boundary_gives_the_prompt_a_full_read_would(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Many turns"}).json()
            _long_transcript(running, chat["id"], count=4)
            for index in range(12):
                started = running.client.post(
                    f"/api/v1/chats/{chat['id']}/turns", json={"text": f"turn {index}", "memory_mode": "off"}
                ).json()
                self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")
            context = running.services.context
            factory, secrets = running.services.runtime.session_factory, running.services.runtime.secret_store
            with UnitOfWork(factory, secrets) as uow:
                turns = uow.repo.turns_for_chat(user_id, chat["id"])
                boundary = turns[7].assistant_message_id
                uow.repo.add_summary(
                    user_id=user_id,
                    chat_id=chat["id"],
                    previous_summary_id=None,
                    through_message_id=boundary,
                    provider="test",
                    model="test",
                    prompt_version="test",
                    source_digest="test",
                    source_message_count=1,
                    content="Everything up to turn seven.",
                    estimated_tokens=8,
                )
            with UnitOfWork(factory, secrets) as uow:
                summary = context._durable_summary(uow, user_id, chat["id"])
                arguments = dict(
                    user_id=user_id, chat_id=chat["id"], before_sequence=None, before_created_at=None, summary=summary
                )
                bounded, earlier = context._load_history(uow, **arguments)
                full, nothing_earlier = context._load_history(uow, bounded=False, **arguments)

            self.assertEqual(nothing_earlier, 0)
            self.assertLess(len(bounded), len(full))
            self.assertEqual(earlier + len(bounded), len(full))
            self.assertEqual(
                context._history_after_summary(bounded, summary),
                context._history_after_summary(full, summary),
            )


class BackgroundWorkKeepsTheLoadedModelTests(unittest.TestCase):
    def test_a_follow_up_on_the_reply_model_asks_for_the_reply_context(self):
        provider = FakeChatProvider(["hi"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "what is new", "memory_mode": "off", "model_settings": {"context_window_tokens": 6144}},
            ).json()
            completed = running.wait_job(started["job"]["id"])
            running.wait_job(completed["result"]["title_job_id"])
            reply = provider.requests[0]
            title = next(request for request in provider.task_requests if request.model == reply.model)
            self.assertEqual(reply.options["num_ctx"], 6144)
            # Anything else would make Ollama unload the model and load it
            # again, and the next reply would wait for that.
            self.assertEqual(title.options["num_ctx"], reply.options["num_ctx"])
            # A local model is never handed the cloud account key.
            self.assertNotIn("api_key", title.options)

    def test_only_the_same_model_on_the_same_server_shares_a_context(self):
        provider = FakeChatProvider()
        loaded = LoadedModel(provider_endpoint(provider), "chat-model", 8192)
        self.assertEqual(shared_context_window(2688, provider, "chat-model", loaded), 8192)
        # A different model would only grow its own allocation for nothing.
        self.assertEqual(shared_context_window(2688, provider, "small-model", loaded), 2688)
        # A task that needs more than the reply loaded cannot share it.
        self.assertEqual(shared_context_window(16384, provider, "chat-model", loaded), 16384)
        elsewhere = LoadedModel("http://other-host:11434", "chat-model", 8192)
        self.assertEqual(shared_context_window(2688, provider, "chat-model", elsewhere), 2688)
        self.assertEqual(shared_context_window(2688, provider, "chat-model", None), 2688)


class _ColdModelProvider(FakeChatProvider):
    """Ollama's last frame says how long it spent loading the model."""

    def stream(self, request, cancellation):
        self.requests.append(request)
        yield ChatDelta("Hello", done=False)
        yield ChatDelta(" there.", done=True, metadata={"load_duration": 2_500_000_000, "prompt_eval_count": 12})


class TheVoiceLoopIsTimedTests(unittest.TestCase):
    def test_a_turn_records_each_stage_and_says_so_once_in_the_log(self):
        provider = _ColdModelProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            # At INFO whatever LOG_LEVEL this run was started with; the
            # verifier quiets the log, and the line is still made.
            with self.assertLogs(running.services.runtime.logger, level="INFO") as captured:
                started = running.client.post(
                    f"/api/v1/chats/{chat['id']}/turns", json={"text": "hello", "memory_mode": "off"}
                ).json()
                self.assertEqual(running.wait_job(started["job"]["id"])["status"], "completed")
            stages = running.client.get("/api/v1/admin/observability").json()["stages"]

        for name in ("turn.waiting", "turn.prompt", "turn.first_token", "turn.complete"):
            self.assertEqual(stages[name]["count"], 1, name)
        self.assertLessEqual(stages["turn.first_token"]["max"], stages["turn.complete"]["max"])
        # Seconds spent loading a model are the reload ADR 0043 set out to
        # stop, so they are counted where somebody will look.
        self.assertEqual(stages["turn.model_load"]["max"], 2500)
        self.assertEqual(stages["turn.model_load"]["recent_p95"], 2500)
        timing_lines = [line for line in captured.output if "turn timing" in line]
        self.assertEqual(len(timing_lines), 1)
        self.assertIn(f"turn_id={started['turn']['id']}", timing_lines[0])
        self.assertIn("model_load_ms=2500", timing_lines[0])

    def test_a_transcription_records_how_long_it_took(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"stt_provider": "local", "preferences": {"stt_local_base_url": "http://127.0.0.1:8000"}},
            )

            def converted(command, **_kwargs):
                Path(command[-1]).write_bytes(b"RIFFfake")
                return mock.Mock(returncode=0)

            with mock.patch("app.speech_service.subprocess.run", side_effect=converted):
                with mock.patch.object(running.services.speech, "_transcribe_with", return_value={"text": "hello"}):
                    response = running.client.post(
                        "/api/v1/speech/transcriptions",
                        files={"file": ("turn.webm", b"webm bytes", "audio/webm")},
                    )
            stages = running.client.get("/api/v1/admin/observability").json()["stages"]

        self.assertEqual(response.json()["text"], "hello")
        self.assertEqual(stages["speech.transcription"]["count"], 1)
        self.assertEqual(stages["speech.conversion"]["count"], 1)

    def test_a_logged_error_says_where_it_was_raised_and_nothing_more(self):
        try:
            raise ValueError("secret-looking detail")
        except ValueError:
            record = logging.LogRecord("t", logging.ERROR, __file__, 1, "job failed", None, sys.exc_info())
        line = json.loads(RedactedJsonFormatter().format(record))["message"]
        self.assertIn("exception=ValueError at test_reply_path.py:", line)
        self.assertNotIn("secret-looking detail", line)
        self.assertNotIn(str(Path(__file__).parent), line)

    def test_percentiles_come_from_recent_turns(self):
        metrics = MetricsRegistry()
        for value in range(1, 101):
            metrics.stage("turn.first_token", value)
        stage = metrics.snapshot()["stages"]["turn.first_token"]
        self.assertEqual((stage["recent_p50"], stage["recent_p95"], stage["max"]), (50, 95, 100))


if __name__ == "__main__":
    unittest.main()
