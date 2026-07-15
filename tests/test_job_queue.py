import threading
import unittest

from app.job_queue import JobQueue, new_job


class JobQueueIsolationTests(unittest.TestCase):
    def test_ordering_key_serializes_one_chat_without_blocking_another(self):
        queue = JobQueue(worker_counts={"interactive": 2, "media": 0})
        release = threading.Event()
        first_started = threading.Event()
        second_started = threading.Event()
        other_started = threading.Event()

        def first():
            first_started.set()
            release.wait(2)

        first_job = queue.submit(
            new_job(
                job_type="chat",
                user_id="u",
                chat_id="a",
                estimated_vram_mb=0,
                latency_class="interactive",
                metadata={"ordering_key": "chat:a"},
                execute=first,
            )
        )
        second_job = queue.submit(
            new_job(
                job_type="chat",
                user_id="u",
                chat_id="a",
                estimated_vram_mb=0,
                latency_class="interactive",
                metadata={"ordering_key": "chat:a"},
                execute=second_started.set,
            )
        )
        other_job = queue.submit(
            new_job(
                job_type="chat",
                user_id="u",
                chat_id="b",
                estimated_vram_mb=0,
                latency_class="interactive",
                metadata={"ordering_key": "chat:b"},
                execute=other_started.set,
            )
        )
        try:
            self.assertTrue(first_started.wait(1))
            self.assertTrue(other_started.wait(1))
            self.assertFalse(second_started.wait(0.1))
            release.set()
            first_job.wait(1)
            second_job.wait(1)
            other_job.wait(1)
            self.assertTrue(second_started.is_set())
        finally:
            release.set()
            queue.stop()

    def test_wait_until_idle_covers_executing_work(self):
        queue = JobQueue(worker_counts={"interactive": 1, "media": 0})
        started = threading.Event()
        release = threading.Event()
        try:
            queue.submit(
                new_job(
                    job_type="chat",
                    user_id="u1",
                    chat_id="c1",
                    estimated_vram_mb=0,
                    latency_class="interactive",
                    execute=lambda: (started.set(), release.wait(timeout=2)),
                )
            )
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(queue.wait_until_idle(timeout=0.01))
            release.set()
            self.assertTrue(queue.wait_until_idle(timeout=1))
        finally:
            release.set()
            queue.stop()

    def test_interactive_job_runs_while_media_job_is_blocked(self):
        queue = JobQueue(worker_counts={"interactive": 1, "media": 1})
        media_started = threading.Event()
        release_media = threading.Event()

        def slow_media():
            media_started.set()
            release_media.wait(timeout=5)
            return "media done"

        media_job = queue.submit(
            new_job(
                job_type="image",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="standard",
                execute=slow_media,
            )
        )
        self.assertTrue(media_started.wait(timeout=1))

        chat_job = queue.submit(
            new_job(
                job_type="chat",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="interactive",
                execute=lambda: "chat done",
            )
        )

        try:
            self.assertEqual(chat_job.wait(timeout=1), "chat done")
            self.assertFalse(media_job.done_event.is_set())
        finally:
            release_media.set()
            self.assertEqual(media_job.wait(timeout=1), "media done")
            queue.stop()

    def test_admission_blocked_media_job_does_not_consume_worker(self):
        admitted = set()
        blocked_started = threading.Event()
        allowed_started = threading.Event()
        queue = JobQueue(
            worker_counts={"interactive": 0, "media": 1},
            admission_check=lambda job: job.id in admitted,
        )
        blocked = new_job(
            job_type="image",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="standard",
            execute=lambda: blocked_started.set(),
        )
        allowed = new_job(
            job_type="image",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="standard",
            execute=lambda: allowed_started.set(),
        )
        admitted.add(allowed.id)
        try:
            queue.submit(blocked)
            queue.submit(allowed)
            self.assertTrue(allowed_started.wait(timeout=1))
            self.assertFalse(blocked_started.is_set())
            admitted.add(blocked.id)
            queue.wake()
            blocked.wait(timeout=1)
            self.assertTrue(blocked_started.is_set())
        finally:
            queue.stop()

    def test_queue_position_is_scoped_to_matching_lane(self):
        queue = JobQueue(worker_counts={"interactive": 1, "media": 1})
        block_interactive = threading.Event()
        block_media = threading.Event()
        interactive_started = threading.Event()
        media_started = threading.Event()

        def wait_interactive():
            interactive_started.set()
            return block_interactive.wait(timeout=5)

        def wait_media():
            media_started.set()
            return block_media.wait(timeout=5)

        interactive_running = queue.submit(
            new_job(
                job_type="chat",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="interactive",
                execute=wait_interactive,
            )
        )
        media_running = queue.submit(
            new_job(
                job_type="image",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="standard",
                execute=wait_media,
            )
        )
        self.assertTrue(interactive_started.wait(timeout=1))
        self.assertTrue(media_started.wait(timeout=1))

        queued_media = queue.submit(
            new_job(
                job_type="image",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="standard",
                metadata={"async_job_id": "media-queued"},
                execute=lambda: "media queued",
            )
        )
        queued_chat = queue.submit(
            new_job(
                job_type="chat",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=1,
                latency_class="interactive",
                metadata={"async_job_id": "chat-queued"},
                execute=lambda: "chat queued",
            )
        )

        try:
            self.assertEqual(queue.queue_position_for_metadata("async_job_id", "media-queued"), 0)
            self.assertEqual(queue.queue_position_for_metadata("async_job_id", "chat-queued"), 0)
        finally:
            block_interactive.set()
            block_media.set()
            queued_chat.wait(timeout=1)
            queued_media.wait(timeout=1)
            interactive_running.wait(timeout=1)
            media_running.wait(timeout=1)
            queue.stop()


if __name__ == "__main__":
    unittest.main()
