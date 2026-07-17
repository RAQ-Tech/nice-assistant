import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


DEFAULT_MAX_WAIT_SECONDS = {
    "interactive": 2.0,
    "standard": 5.0,
    "bulk": 10.0,
}

LATENCY_PRIORITY = {
    "interactive": 0,
    "standard": 1,
    "bulk": 2,
}

JOB_TYPE_LANES = {
    "chat": "interactive",
    "text": "interactive",
    "memory_extraction": "interactive",
    "task_model": "interactive",
    "image": "media",
    "video": "media",
}

DEFAULT_WORKER_COUNTS = {
    "interactive": 1,
    "media": 1,
}


@dataclass
class JobResult:
    value: Any = None
    error: Optional[BaseException] = None


@dataclass
class Job:
    job_type: str
    user_id: str
    chat_id: Optional[str]
    estimated_vram_mb: int
    latency_class: str
    arrival_time: float
    execute: Callable[[], Any]
    model_key: Optional[str] = None
    group_id: Optional[str] = None
    group_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    done_event: threading.Event = field(default_factory=threading.Event)
    result: JobResult = field(default_factory=JobResult)

    def mark_done(self, value: Any = None, error: Optional[BaseException] = None):
        self.result.value = value
        self.result.error = error
        self.done_event.set()

    def wait(self, timeout: Optional[float] = None) -> Any:
        ok = self.done_event.wait(timeout=timeout)
        if not ok:
            raise TimeoutError(f"job timed out: {self.id}")
        if self.result.error:
            raise self.result.error
        return self.result.value


class JobQueue:
    def __init__(
        self,
        max_wait_seconds: Optional[Dict[str, float]] = None,
        worker_counts: Optional[Dict[str, int]] = None,
        admission_check: Callable[[Job], bool] | None = None,
        on_selected: Callable[[Job], None] | None = None,
        serialize_resources: Callable[[], bool] | None = None,
    ):
        self.max_wait_seconds = {**DEFAULT_MAX_WAIT_SECONDS, **(max_wait_seconds or {})}
        self.worker_counts = self._normalize_worker_counts(worker_counts)
        self.admission_check = admission_check or (lambda _job: True)
        self.on_selected = on_selected or (lambda _job: None)
        self.serialize_resources = serialize_resources or (lambda: False)
        self._pending: List[Job] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = False
        self._current_model_key_by_lane: Dict[str, Optional[str]] = {}
        self._active_by_lane: Dict[str, int] = {}
        self._active_ordering_keys: set[str] = set()
        self._workers: List[threading.Thread] = []
        self._stopped_pending: List[Job] = []
        for lane, count in self.worker_counts.items():
            for idx in range(count):
                worker = threading.Thread(
                    target=self._run,
                    args=(lane,),
                    name=f"job-queue-{lane}-{idx + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def _normalize_worker_counts(self, worker_counts: Optional[Dict[str, int]]) -> Dict[str, int]:
        normalized = {**DEFAULT_WORKER_COUNTS}
        if worker_counts:
            for lane, count in worker_counts.items():
                try:
                    normalized[lane] = max(0, int(count))
                except (TypeError, ValueError):
                    normalized[lane] = 0
        if not any(count > 0 for count in normalized.values()):
            normalized["interactive"] = 1
        return {lane: count for lane, count in normalized.items() if count > 0}

    def submit(self, job: Job) -> Job:
        with self._cv:
            if self._stop:
                raise RuntimeError("job queue stopped")
            self._pending.append(job)
            self._cv.notify_all()
        return job

    def submit_group(self, jobs: List[Job]) -> List[Job]:
        group_id = uuid.uuid4().hex
        for idx, job in enumerate(jobs):
            job.group_id = group_id
            job.group_index = idx
        with self._cv:
            if self._stop:
                raise RuntimeError("job queue stopped")
            self._pending.extend(jobs)
            self._cv.notify_all()
        return jobs

    def wake(self) -> None:
        with self._cv:
            self._cv.notify_all()

    def queue_position_for_metadata(self, key: str, value: Any) -> Optional[int]:
        with self._lock:
            target_lane = None
            for job in self._pending:
                if job.metadata.get(key) == value:
                    target_lane = self._lane_for_job(job)
                    break
            if target_lane is None:
                return None
            lane_jobs = [job for job in self._pending if self._lane_for_job(job) == target_lane]
            for idx, job in enumerate(lane_jobs):
                if job.metadata.get(key) == value:
                    return idx
        return None

    def cancel_pending_for_metadata(self, key: str, value: Any) -> bool:
        """Remove a queued job before a worker starts it."""
        with self._cv:
            for job in list(self._pending):
                if job.metadata.get(key) != value:
                    continue
                self._pending.remove(job)
                job.mark_done(error=RuntimeError("job cancelled"))
                self._cv.notify_all()
                return True
        return False

    def close_and_detach_pending(self) -> List[Job]:
        """Atomically reject new work and detach jobs no worker has selected."""
        with self._cv:
            self._stop = True
            pending = list(self._pending)
            self._pending.clear()
            self._stopped_pending.extend(pending)
            for job in pending:
                job.mark_done(error=RuntimeError("job queue stopped"))
            self._cv.notify_all()
        return pending

    def join_stopped_workers(self, wait=True) -> List[Job]:
        """Join workers after the owner has cancelled and terminalized detached work."""
        for worker in self._workers:
            worker.join(timeout=10)
        if wait and not self.wait_until_idle(timeout=10):
            raise RuntimeError("job queue did not become idle")
        return self.stopped_pending_jobs()

    def stop(self) -> List[Job]:
        pending = self.close_and_detach_pending()
        self.join_stopped_workers(wait=False)
        return pending

    def shutdown(self, wait=True) -> List[Job]:
        self.close_and_detach_pending()
        return self.join_stopped_workers(wait=wait)

    def stopped_pending_jobs(self) -> List[Job]:
        """Return accepted jobs removed by shutdown but not yet acknowledged."""
        with self._cv:
            return list(self._stopped_pending)

    def acknowledge_stopped_pending(self, job_id: str) -> None:
        """Forget one stopped job after its owner terminalizes durable state."""
        with self._cv:
            self._stopped_pending = [job for job in self._stopped_pending if job.id != job_id]

    def wait_until_idle(self, timeout: Optional[float] = None) -> bool:
        """Wait until no queued or executing work remains."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cv:
            while self._pending or any(self._active_by_lane.values()):
                if deadline is None:
                    self._cv.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(timeout=remaining)
            return True

    def snapshot(self) -> dict:
        with self._lock:
            pending = Counter(self._lane_for_job(job) for job in self._pending)
            active = dict(self._active_by_lane)
            return {
                "pending": {lane: int(pending.get(lane, 0)) for lane in sorted(self.worker_counts)},
                "active": {lane: int(active.get(lane, 0)) for lane in sorted(self.worker_counts)},
                "workers": dict(self.worker_counts),
            }

    def _lane_for_job(self, job: Job) -> str:
        metadata_lane = str((job.metadata or {}).get("queue_lane") or "").strip().lower()
        if metadata_lane:
            return metadata_lane
        if job.job_type in JOB_TYPE_LANES:
            return JOB_TYPE_LANES[job.job_type]
        if job.latency_class == "interactive":
            return "interactive"
        return "media"

    def _run(self, lane: str):
        while True:
            with self._cv:
                while not self._eligible_pending_for_lane_locked(lane) and not self._stop:
                    self._cv.wait(timeout=0.5)
                if self._stop:
                    return
                job = self._pick_next_job_locked(lane)
                self.on_selected(job)
                self._active_by_lane[lane] = self._active_by_lane.get(lane, 0) + 1
                ordering_key = str(job.metadata.get("ordering_key") or "")
                if ordering_key:
                    self._active_ordering_keys.add(ordering_key)
            try:
                value = job.execute()
                job.mark_done(value=value)
            except BaseException as err:  # noqa: BLE001 - pass through execution failures
                job.mark_done(error=err)
            finally:
                with self._cv:
                    self._active_by_lane[lane] = max(0, self._active_by_lane.get(lane, 1) - 1)
                    if ordering_key:
                        self._active_ordering_keys.discard(ordering_key)
                    self._cv.notify_all()

    def _pending_for_lane_locked(self, lane: str) -> List[Job]:
        return [job for job in self._pending if self._lane_for_job(job) == lane]

    def _eligible_pending_for_lane_locked(self, lane: str) -> List[Job]:
        if lane == "media" and self.serialize_resources():
            interactive_waiting = any(
                bool(job.metadata.get("coordinated_resource")) for job in self._pending_for_lane_locked("interactive")
            )
            if interactive_waiting:
                return []
        eligible = []
        seen_ordering_keys = set()
        for job in sorted(self._pending_for_lane_locked(lane), key=lambda item: item.arrival_time):
            if not self.admission_check(job):
                continue
            ordering_key = str(job.metadata.get("ordering_key") or "")
            if not ordering_key:
                eligible.append(job)
                continue
            if ordering_key in seen_ordering_keys:
                continue
            seen_ordering_keys.add(ordering_key)
            if ordering_key not in self._active_ordering_keys:
                eligible.append(job)
        return eligible

    def _pick_next_job_locked(self, lane: str) -> Job:
        lane_pending = self._eligible_pending_for_lane_locked(lane)
        queue_depth = len(lane_pending)
        now = time.time()

        # Starvation prevention: promote jobs that exceeded max wait.
        overdue_candidates: List[Job] = []
        for job in lane_pending:
            wait_limit = self.max_wait_seconds.get(job.latency_class, self.max_wait_seconds["standard"])
            if now - job.arrival_time >= wait_limit:
                overdue_candidates.append(job)
        if overdue_candidates:
            selected = min(overdue_candidates, key=lambda j: j.arrival_time)
            self._pending.remove(selected)
            return selected

        # Grouped completion optimization: for text+image groups, schedule slower image first.
        group = self._next_text_image_group(lane)
        if group:
            image_job = next((j for j in group if j.job_type == "image"), None)
            if image_job:
                self._pending.remove(image_job)
                self._current_model_key_by_lane[lane] = image_job.model_key
                return image_job

        current_model_key = self._current_model_key_by_lane.get(lane)
        if queue_depth > 1 and current_model_key:
            for job in lane_pending:
                if job.model_key and job.model_key == current_model_key:
                    self._pending.remove(job)
                    return job

        selected = min(
            lane_pending,
            key=lambda j: (LATENCY_PRIORITY.get(j.latency_class, 1), j.arrival_time),
        )
        self._pending.remove(selected)
        self._current_model_key_by_lane[lane] = selected.model_key
        return selected

    def _next_text_image_group(self, lane: str) -> Optional[List[Job]]:
        if lane != "media":
            return None
        groups: Dict[str, List[Job]] = {}
        for job in self._pending:
            if not job.group_id:
                continue
            groups.setdefault(job.group_id, []).append(job)
        for jobs in groups.values():
            types = {j.job_type for j in jobs}
            if "text" in types and "image" in types:
                return sorted(jobs, key=lambda j: j.arrival_time)
        return None


def new_job(
    *,
    job_type: str,
    user_id: str,
    chat_id: Optional[str],
    estimated_vram_mb: int,
    latency_class: str,
    execute: Callable[[], Any],
    model_key: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Job:
    return Job(
        job_type=job_type,
        user_id=user_id,
        chat_id=chat_id,
        estimated_vram_mb=estimated_vram_mb,
        latency_class=latency_class,
        arrival_time=time.time(),
        execute=execute,
        model_key=model_key,
        metadata=metadata or {},
    )
