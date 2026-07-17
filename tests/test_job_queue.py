import threading
import unittest
from unittest.mock import Mock

from app.job_queue import JobQueue, new_job
from app.job_service import JobExecution, JobService


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

    def test_stopped_queue_rejects_new_work(self):
        queue = JobQueue(worker_counts={"interactive": 1, "media": 0})
        queue.stop()

        with self.assertRaisesRegex(RuntimeError, "job queue stopped"):
            queue.submit(
                new_job(
                    job_type="chat",
                    user_id="u1",
                    chat_id=None,
                    estimated_vram_mb=0,
                    latency_class="interactive",
                    execute=lambda: None,
                )
            )

    def test_stop_returns_the_exact_pending_jobs_until_the_owner_acknowledges_them(self):
        queue = JobQueue(
            worker_counts={"interactive": 1, "media": 0},
            admission_check=lambda _job: False,
        )
        first = queue.submit(
            new_job(
                job_type="chat",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=0,
                latency_class="interactive",
                execute=lambda: None,
            )
        )
        second = queue.submit(
            new_job(
                job_type="chat",
                user_id="u1",
                chat_id=None,
                estimated_vram_mb=0,
                latency_class="interactive",
                execute=lambda: None,
            )
        )

        stopped = queue.stop()

        self.assertEqual([job.id for job in stopped], [first.id, second.id])
        self.assertEqual(
            [job.id for job in queue.stopped_pending_jobs()],
            [first.id, second.id],
        )
        queue.acknowledge_stopped_pending(first.id)
        self.assertEqual([job.id for job in queue.stopped_pending_jobs()], [second.id])


class JobServiceLifecycleTests(unittest.TestCase):
    def _service(self, *, resource_coordinator=None):
        return JobService(
            session_factory=None,
            secret_store=None,
            broker=Mock(),
            logger=Mock(),
            worker_counts={"interactive": 1, "media": 0},
            resource_coordinator=resource_coordinator,
        )

    def test_stop_rejects_a_concurrent_followup_submission(self):
        service = self._service()
        service.start()
        queue = service.queue
        shutdown_started = threading.Event()
        release_shutdown = threading.Event()
        original_join = queue.join_stopped_workers

        def controlled_join(*, wait=True):
            shutdown_started.set()
            self.assertTrue(release_shutdown.wait(timeout=2))
            return original_join(wait=wait)

        queue.join_stopped_workers = controlled_join
        stop_thread = threading.Thread(target=service.stop)
        stop_thread.start()
        try:
            self.assertTrue(shutdown_started.wait(timeout=1))
            with self.assertRaisesRegex(RuntimeError, "not accepting submissions"):
                service.submit(
                    job_id="late-followup",
                    job_type="image",
                    user_id="u1",
                    chat_id="c1",
                    turn_id=None,
                    latency_class="standard",
                    model_key="image:test",
                    execution=JobExecution(execute=lambda _token: None),
                )
            self.assertNotIn("late-followup", service._tokens)
            self.assertNotIn("late-followup", service._done)
            self.assertNotIn("late-followup", service._executions)
        finally:
            release_shutdown.set()
            stop_thread.join(timeout=2)
        self.assertFalse(stop_thread.is_alive())

    def test_queue_rejection_discards_registered_submission_state(self):
        service = self._service()
        service.start()
        service.queue.stop()
        try:
            with self.assertRaisesRegex(RuntimeError, "job queue stopped"):
                service.submit(
                    job_id="rejected",
                    job_type="image",
                    user_id="u1",
                    chat_id="c1",
                    turn_id=None,
                    latency_class="standard",
                    model_key="image:test",
                    execution=JobExecution(execute=lambda _token: None),
                )
            self.assertNotIn("rejected", service._tokens)
            self.assertNotIn("rejected", service._done)
            self.assertNotIn("rejected", service._executions)
        finally:
            service.stop()

    def test_overlapping_stop_waits_for_the_same_shutdown(self):
        service = self._service()
        service.start()
        queue = service.queue
        shutdown_started = threading.Event()
        release_shutdown = threading.Event()
        original_join = queue.join_stopped_workers

        def controlled_join(*, wait=True):
            shutdown_started.set()
            self.assertTrue(release_shutdown.wait(timeout=2))
            return original_join(wait=wait)

        queue.join_stopped_workers = controlled_join
        first_stop = threading.Thread(target=service.stop)
        second_stop = threading.Thread(target=service.stop)
        first_stop.start()
        self.assertTrue(shutdown_started.wait(timeout=1))
        second_stop.start()
        try:
            second_stop.join(timeout=0.05)
            self.assertTrue(second_stop.is_alive())
        finally:
            release_shutdown.set()
            first_stop.join(timeout=2)
            second_stop.join(timeout=2)
        self.assertFalse(first_stop.is_alive())
        self.assertFalse(second_stop.is_alive())
        self.assertIsNone(service.queue)

    def test_start_waits_for_an_in_progress_stop(self):
        service = self._service()
        service.start()
        old_queue = service.queue
        shutdown_started = threading.Event()
        release_shutdown = threading.Event()
        original_join = old_queue.join_stopped_workers

        def controlled_join(*, wait=True):
            shutdown_started.set()
            self.assertTrue(release_shutdown.wait(timeout=2))
            return original_join(wait=wait)

        old_queue.join_stopped_workers = controlled_join
        stop_thread = threading.Thread(target=service.stop)
        start_thread = threading.Thread(target=service.start)
        stop_thread.start()
        self.assertTrue(shutdown_started.wait(timeout=1))
        start_thread.start()
        try:
            start_thread.join(timeout=0.05)
            self.assertTrue(start_thread.is_alive())
        finally:
            release_shutdown.set()
            stop_thread.join(timeout=2)
            start_thread.join(timeout=2)
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(start_thread.is_alive())
        self.assertIsNotNone(service.queue)
        self.assertIsNot(service.queue, old_queue)
        service.stop()

    def test_failed_shutdown_retains_the_old_queue_and_blocks_restart_until_retry(self):
        service = self._service()
        service.start()
        old_queue = service.queue
        original_join = old_queue.join_stopped_workers
        attempts = 0

        def fail_once(*, wait=True):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulated shutdown timeout")
            return original_join(wait=wait)

        old_queue.join_stopped_workers = fail_once
        with self.assertRaisesRegex(RuntimeError, "simulated shutdown timeout"):
            service.stop()

        self.assertIs(service.queue, old_queue)
        with self.assertRaisesRegex(RuntimeError, "cannot restart after a failed shutdown"):
            service.start()

        service.stop()
        self.assertIsNone(service.queue)
        service.start()
        self.assertIsNot(service.queue, old_queue)
        service.stop()

    def test_stopped_pending_job_is_terminalized_and_releases_coordination_once(self):
        coordinator = Mock()
        coordinator.can_start.return_value = False
        coordinator.enabled = False
        service = self._service(resource_coordinator=coordinator)
        service._cancel_terminal = Mock()
        service.start()
        service.submit(
            job_id="accepted-pending",
            job_type="image",
            user_id="u1",
            chat_id="c1",
            turn_id=None,
            latency_class="standard",
            model_key="image:test",
            execution=JobExecution(execute=lambda _token: None),
        )

        service.stop()

        service._cancel_terminal.assert_called_once_with("accepted-pending", None, None)
        coordinator.cancel.assert_called_once_with("accepted-pending")
        self.assertNotIn("accepted-pending", service._tokens)
        self.assertNotIn("accepted-pending", service._done)
        self.assertNotIn("accepted-pending", service._executions)

    def test_queue_is_closed_before_coordinator_cancellation_can_wake_pending_work(self):
        cancel_started = threading.Event()
        release_cancel = threading.Event()
        job_selected = threading.Event()

        class WakeOnCancelCoordinator:
            enabled = True

            def __init__(self):
                self.admitted = False
                self.cancelled = []
                self.wake_queue = lambda: None

            def bind_queue_wake(self, callback):
                self.wake_queue = callback

            def can_start(self, _job):
                return self.admitted

            def reserve(self, _job):
                job_selected.set()
                return None

            def register(self, *_args, **_kwargs):
                return None

            def cancel(self, job_id):
                self.cancelled.append(job_id)
                self.admitted = True
                self.wake_queue()
                cancel_started.set()
                self.assert_release()

            def assert_release(self):
                if not release_cancel.wait(timeout=2):
                    raise AssertionError("coordinator cancellation was not released")

            def execution_started(self, _job_id):
                return None

            def complete(self, _queue_job_id, _job_id):
                return None

        coordinator = WakeOnCancelCoordinator()
        service = self._service(resource_coordinator=coordinator)
        service._cancel_terminal = Mock()
        service.start()
        service.submit(
            job_id="blocked-pending",
            job_type="task_model",
            user_id="u1",
            chat_id="c1",
            turn_id=None,
            latency_class="standard",
            model_key="task:test",
            execution=JobExecution(execute=lambda _token: None),
        )
        stop_thread = threading.Thread(target=service.stop)
        stop_thread.start()
        try:
            self.assertTrue(cancel_started.wait(timeout=1))
            self.assertFalse(job_selected.wait(timeout=1))
        finally:
            release_cancel.set()
            stop_thread.join(timeout=2)

        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(job_selected.is_set())
        self.assertEqual(coordinator.cancelled, ["blocked-pending"])
        service._cancel_terminal.assert_called_once_with("blocked-pending", None, None)


if __name__ == "__main__":
    unittest.main()
