import threading
import unittest

from app.job_queue import JobQueue, new_job


class JobQueueIsolationTests(unittest.TestCase):
    def test_interactive_job_runs_while_media_job_is_blocked(self):
        queue = JobQueue(worker_counts={"interactive": 1, "media": 1})
        media_started = threading.Event()
        release_media = threading.Event()

        def slow_media():
            media_started.set()
            release_media.wait(timeout=5)
            return "media done"

        media_job = queue.submit(new_job(
            job_type="image",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="standard",
            execute=slow_media,
        ))
        self.assertTrue(media_started.wait(timeout=1))

        chat_job = queue.submit(new_job(
            job_type="chat",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="interactive",
            execute=lambda: "chat done",
        ))

        try:
            self.assertEqual(chat_job.wait(timeout=1), "chat done")
            self.assertFalse(media_job.done_event.is_set())
        finally:
            release_media.set()
            self.assertEqual(media_job.wait(timeout=1), "media done")
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

        interactive_running = queue.submit(new_job(
            job_type="chat",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="interactive",
            execute=wait_interactive,
        ))
        media_running = queue.submit(new_job(
            job_type="image",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="standard",
            execute=wait_media,
        ))
        self.assertTrue(interactive_started.wait(timeout=1))
        self.assertTrue(media_started.wait(timeout=1))

        queued_media = queue.submit(new_job(
            job_type="image",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="standard",
            metadata={"async_job_id": "media-queued"},
            execute=lambda: "media queued",
        ))
        queued_chat = queue.submit(new_job(
            job_type="chat",
            user_id="u1",
            chat_id=None,
            estimated_vram_mb=1,
            latency_class="interactive",
            metadata={"async_job_id": "chat-queued"},
            execute=lambda: "chat queued",
        ))

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
