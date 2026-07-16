from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time

from app.auth import redact_sensitive_text
from app.job_queue import JobQueue, new_job
from app.provider_contracts import CancellationToken, ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ServiceError
from app.turn_events import TurnEventBroker


TERMINAL_STATES = {"completed", "failed", "cancelled"}
LEGAL_TRANSITIONS = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


@dataclass
class JobExecution:
    execute: object
    on_start: object | None = None
    on_success: object | None = None
    on_failure: object | None = None
    on_cancel: object | None = None
    after_success: object | None = None


class InvalidJobTransition(RuntimeError):
    pass


def transition_job(job, state: str, *, progress: str, error: str | None = None) -> None:
    if state == job.status:
        return
    if state not in LEGAL_TRANSITIONS.get(job.status, set()):
        raise InvalidJobTransition(f"invalid job transition: {job.status} -> {state}")
    stamp = now_ts()
    job.status = state
    job.progress = progress
    job.updated_at = stamp
    if state == "running":
        job.started_at = stamp
    if state in TERMINAL_STATES:
        job.completed_at = stamp
    if error is not None:
        job.error = error


def transition_turn(turn, state: str, *, code: str | None = None, message: str | None = None) -> None:
    if state == turn.status:
        return
    if state not in LEGAL_TRANSITIONS.get(turn.status, set()):
        raise InvalidJobTransition(f"invalid turn transition: {turn.status} -> {state}")
    stamp = now_ts()
    turn.status = state
    if state == "running":
        turn.started_at = stamp
    if state in TERMINAL_STATES:
        turn.completed_at = stamp
    turn.error_code = code
    turn.error_message = message


def job_response(job, queue_position=None) -> dict:
    result = None
    if job.result_json:
        try:
            result = json.loads(job.result_json)
        except (TypeError, ValueError):
            result = None
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "chat_id": job.chat_id,
        "turn_id": job.turn_id,
        "capability_request_id": job.capability_request_id,
        "progress": job.progress or "",
        "queue_position": queue_position,
        "result": result,
        "error": job.error or "",
        "cancel_requested": bool(job.cancel_requested),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


def turn_response(turn, job_id: str | None = None, accumulated_text: str = "") -> dict:
    error = None
    if turn.error_code or turn.error_message:
        error = {"code": turn.error_code or "failed", "message": turn.error_message or "Turn failed."}
    context = {
        "context_window_tokens": turn.context_window_tokens,
        "prompt_budget_tokens": turn.prompt_budget_tokens,
        "prompt_tokens_estimated": turn.prompt_tokens_estimated,
        "prompt_tokens_actual": turn.prompt_tokens_actual,
        "included_message_count": turn.included_message_count,
        "omitted_message_count": turn.omitted_message_count,
        "included_memory_count": turn.included_memory_count,
        "omitted_memory_count": turn.omitted_memory_count,
        "summary_id": turn.context_summary_id,
        "degraded_reason": turn.context_degraded_reason,
    }
    return {
        "id": turn.id,
        "chat_id": turn.chat_id,
        "job_id": job_id,
        "status": turn.status,
        "provider": turn.provider,
        "model": turn.model,
        "user_message_id": turn.user_message_id,
        "assistant_message_id": turn.assistant_message_id,
        "accumulated_text": accumulated_text,
        "error": error,
        "created_at": turn.created_at,
        "started_at": turn.started_at,
        "completed_at": turn.completed_at,
        "context": context if any(value is not None for value in context.values()) else None,
    }


class JobService:
    def __init__(
        self,
        session_factory,
        secret_store,
        broker: TurnEventBroker,
        logger,
        worker_counts: dict[str, int],
        resource_coordinator=None,
        metrics=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.broker = broker
        self.logger = logger
        self.worker_counts = worker_counts
        self.resource_coordinator = resource_coordinator
        self.metrics = metrics
        self.queue: JobQueue | None = None
        self._tokens: dict[str, CancellationToken] = {}
        self._done: dict[str, threading.Event] = {}
        self._executions: dict[str, JobExecution] = {}
        self._lock = threading.Lock()

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def start(self) -> None:
        if self.queue is None:
            self.queue = JobQueue(
                worker_counts=self.worker_counts,
                admission_check=(self.resource_coordinator.can_start if self.resource_coordinator else None),
                on_selected=(self.resource_coordinator.reserve if self.resource_coordinator else None),
                serialize_resources=(lambda: self.resource_coordinator.enabled) if self.resource_coordinator else None,
            )
            if self.resource_coordinator:
                self.resource_coordinator.bind_queue_wake(self.queue.wake)

    def stop(self) -> None:
        with self._lock:
            tokens = list(self._tokens.values())
            job_ids = list(self._tokens)
        for token in tokens:
            token.cancel()
        if self.resource_coordinator:
            for job_id in job_ids:
                self.resource_coordinator.cancel(job_id)
        if self.queue:
            self.queue.shutdown(wait=True)
            self.queue = None

    def submit(
        self,
        *,
        job_id: str,
        job_type: str,
        user_id: str,
        chat_id: str | None,
        turn_id: str | None,
        latency_class: str,
        model_key: str | None,
        execution: JobExecution,
        estimated_vram_mb: int = 0,
        resource_request=None,
        ordering_key: str | None = None,
    ) -> None:
        if self.queue is None:
            raise RuntimeError("job service is not started")
        token = CancellationToken()
        done = threading.Event()
        with self._lock:
            self._tokens[job_id] = token
            self._done[job_id] = done
            self._executions[job_id] = execution

        coordinated_resource = job_type in {"chat", "text", "task_model", "memory_extraction"}
        coordinated_resource = coordinated_resource or resource_request is not None
        queue_job = new_job(
            job_type=job_type,
            user_id=user_id,
            chat_id=chat_id,
            estimated_vram_mb=max(0, int(estimated_vram_mb or 0)),
            latency_class=latency_class,
            model_key=model_key,
            metadata={
                "async_job_id": job_id,
                "turn_id": turn_id,
                "ordering_key": ordering_key or (f"chat:{chat_id}" if turn_id and chat_id else ""),
                "coordinated_resource": coordinated_resource,
            },
            execute=lambda: self._run(queue_job.id, job_id, turn_id, token, execution),
        )
        if self.resource_coordinator:
            self.resource_coordinator.register(
                job_id,
                resource_request,
                on_wait=lambda progress: self._admission_wait(job_id, progress),
                on_reject=lambda code, message: self._admission_reject(job_id, code, message, execution),
            )
        self.queue.submit(queue_job)

    def _run(
        self,
        queue_job_id: str,
        job_id: str,
        turn_id: str | None,
        token: CancellationToken,
        execution: JobExecution,
    ):
        try:
            if not self._begin(job_id, turn_id, execution.on_start):
                return None
            if self.resource_coordinator:
                self.resource_coordinator.execution_started(job_id)
            if turn_id:
                self.broker.publish(turn_id, "turn.started", {"turn_id": turn_id, "status": "running"})
            result = execution.execute(token)
            token.raise_if_cancelled()
            completed, completed_result = self._complete(job_id, turn_id, result, execution.on_success)
            if completed and execution.after_success:
                try:
                    execution.after_success(completed_result)
                except Exception as exc:  # noqa: BLE001 - follow-up work cannot invalidate a completed job
                    self.logger.error(
                        "post-completion work failed job_id=%s error=%s",
                        job_id,
                        exc.__class__.__name__,
                    )
            return completed_result
        except ProviderError as exc:
            if exc.code == "cancelled" or token.cancelled:
                self._cancel_terminal(job_id, turn_id, execution.on_cancel)
            else:
                self._fail(job_id, turn_id, exc.code, exc.user_message, execution.on_failure)
            return None
        except ServiceError as exc:
            self._fail(job_id, turn_id, exc.code, exc.message, execution.on_failure)
            return None
        except Exception as exc:  # noqa: BLE001 - normalize worker failures
            self.logger.error("job execution failed job_id=%s error=%s", job_id, exc.__class__.__name__)
            self._fail(
                job_id,
                turn_id,
                "internal_error",
                "The request failed unexpectedly.",
                execution.on_failure,
            )
            return None
        finally:
            if self.resource_coordinator:
                self.resource_coordinator.complete(queue_job_id, job_id)
            with self._lock:
                self._tokens.pop(job_id, None)
                self._executions.pop(job_id, None)
                done = self._done.get(job_id)
            if done:
                done.set()

    def _begin(self, job_id: str, turn_id: str | None, on_start=None) -> bool:
        with self._uow() as uow:
            job = uow.repo.job_by_id(job_id)
            if not job or job.status == "cancelled" or job.cancel_requested:
                return False
            transition_job(job, "running", progress="Running")
            if turn_id:
                turn = uow.repo.turn_by_id(turn_id)
                if turn:
                    transition_turn(turn, "running")
            if on_start:
                on_start(uow.repo)
        return True

    def _complete(self, job_id: str, turn_id: str | None, result, on_success):
        with self._uow() as uow:
            job = uow.repo.job_by_id(job_id)
            if not job or job.status == "cancelled" or job.cancel_requested:
                return False, None
            if on_success:
                result = on_success(uow.repo, result)
            transition_job(job, "completed", progress="Completed")
            self._record_job(job)
            job.result_json = json.dumps(result or {}, default=str)
            if turn_id:
                turn = uow.repo.turn_by_id(turn_id)
                if turn:
                    transition_turn(turn, "completed")
                    event = turn_response(turn, job_id, self.broker.accumulated_text(turn_id))
                else:
                    event = {"id": turn_id, "status": "completed"}
            else:
                event = None
        if turn_id:
            self.broker.publish(turn_id, "turn.completed", event)
        return True, result

    def _fail(self, job_id: str, turn_id: str | None, code: str, message: str, on_failure=None) -> None:
        safe_message = redact_sensitive_text(message)[:1000]
        event = None
        with self._uow() as uow:
            job = uow.repo.job_by_id(job_id)
            if not job or job.status in TERMINAL_STATES:
                return
            transition_job(job, "failed", progress="Failed", error=safe_message)
            self._record_job(job)
            if turn_id:
                turn = uow.repo.turn_by_id(turn_id)
                if turn and turn.status not in TERMINAL_STATES:
                    transition_turn(turn, "failed", code=code, message=safe_message)
                    event = turn_response(turn, job_id, self.broker.accumulated_text(turn_id))
            if on_failure:
                on_failure(uow.repo, code, safe_message)
        if turn_id:
            self.broker.publish(turn_id, "turn.failed", event or {"id": turn_id, "status": "failed"})

    def _cancel_terminal(self, job_id: str, turn_id: str | None, on_cancel=None) -> None:
        event = None
        changed = False
        with self._uow() as uow:
            job = uow.repo.job_by_id(job_id)
            if not job:
                return
            if job.status not in TERMINAL_STATES:
                transition_job(job, "cancelled", progress="Cancelled")
                self._record_job(job)
                changed = True
            job.cancel_requested = 1
            if turn_id:
                turn = uow.repo.turn_by_id(turn_id)
                if turn and turn.status not in TERMINAL_STATES:
                    transition_turn(turn, "cancelled", code="cancelled", message="Request cancelled.")
                    event = turn_response(turn, job_id, self.broker.accumulated_text(turn_id))
                    changed = True
            if on_cancel:
                on_cancel(uow.repo)
        if turn_id and changed:
            self.broker.publish(turn_id, "turn.cancelled", event or {"id": turn_id, "status": "cancelled"})

    def get(self, user_id: str, job_id: str) -> dict | None:
        with self._uow() as uow:
            job = uow.repo.job(user_id, job_id)
            if not job:
                return None
            position = (
                self.queue.queue_position_for_metadata("async_job_id", job_id)
                if self.queue and job.status == "queued"
                else None
            )
            return job_response(job, position)

    def fail_unsubmitted(self, job_id: str, message: str, on_failure=None) -> None:
        """Make an atomically-created follow-up job truthful when queue submission fails."""
        if self.resource_coordinator:
            self.resource_coordinator.cancel(job_id)
        self._fail(job_id, None, "submission_failed", message, on_failure)

    def _admission_wait(self, job_id: str, progress: str) -> None:
        with self._uow() as uow:
            job = uow.repo.job_by_id(job_id)
            if job and job.status == "queued":
                job.progress = progress
                job.updated_at = now_ts()

    def _admission_reject(self, job_id: str, code: str, message: str, execution: JobExecution) -> None:
        if self.queue:
            self.queue.cancel_pending_for_metadata("async_job_id", job_id)
        self._fail(job_id, None, code, message, execution.on_failure)
        with self._lock:
            self._tokens.pop(job_id, None)
            self._executions.pop(job_id, None)
            done = self._done.get(job_id)
        if done:
            done.set()

    def cancel(self, user_id: str, job_id: str) -> dict | None:
        turn_id = None
        changed = False
        with self._lock:
            execution = self._executions.get(job_id)
        with self._uow() as uow:
            job = uow.repo.job(user_id, job_id)
            if not job:
                return None
            turn_id = job.turn_id
            if job.status not in TERMINAL_STATES:
                transition_job(job, "cancelled", progress="Cancelled")
                self._record_job(job)
                job.cancel_requested = 1
                changed = True
                if turn_id:
                    turn = uow.repo.turn_by_id(turn_id)
                    if turn and turn.status not in TERMINAL_STATES:
                        transition_turn(turn, "cancelled", code="cancelled", message="Request cancelled.")
                if execution and execution.on_cancel:
                    execution.on_cancel(uow.repo)
            response = job_response(job)
        with self._lock:
            token = self._tokens.get(job_id)
            done = self._done.get(job_id)
        if token:
            token.cancel()
        if self.resource_coordinator:
            self.resource_coordinator.cancel(job_id)
        removed_pending = False
        if self.queue:
            removed_pending = self.queue.cancel_pending_for_metadata("async_job_id", job_id)
        if removed_pending:
            with self._lock:
                self._tokens.pop(job_id, None)
                self._executions.pop(job_id, None)
        if done:
            done.set()
        if turn_id and changed:
            self.broker.publish(
                turn_id,
                "turn.cancelled",
                {"id": turn_id, "job_id": job_id, "status": "cancelled"},
            )
        return response

    def _record_job(self, job) -> None:
        if not self.metrics:
            return
        started = job.started_at or job.created_at or job.completed_at or now_ts()
        completed = job.completed_at or now_ts()
        self.metrics.job(job.kind, job.status, max(0, int(completed - started) * 1000))

    def operational_snapshot(self) -> dict:
        if not self.queue:
            return {"pending": {}, "active": {}, "workers": {}}
        return self.queue.snapshot()

    def wait(self, user_id: str, job_id: str, timeout: float = 180.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.get(user_id, job_id)
            if not current or current["status"] in TERMINAL_STATES:
                return current
            with self._lock:
                event = self._done.get(job_id)
            if event:
                event.wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
            else:
                time.sleep(0.05)
        raise TimeoutError(f"job timed out: {job_id}")
