import threading
import time
import uuid
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
    ):
        self.max_wait_seconds = {**DEFAULT_MAX_WAIT_SECONDS, **(max_wait_seconds or {})}
        self.worker_counts = self._normalize_worker_counts(worker_counts)
        self._pending: List[Job] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = False
        self._current_model_key_by_lane: Dict[str, Optional[str]] = {}
        self._workers: List[threading.Thread] = []
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
            self._pending.append(job)
            self._cv.notify_all()
        return job

    def submit_group(self, jobs: List[Job]) -> List[Job]:
        group_id = uuid.uuid4().hex
        for idx, job in enumerate(jobs):
            job.group_id = group_id
            job.group_index = idx
        with self._cv:
            self._pending.extend(jobs)
            self._cv.notify_all()
        return jobs

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

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        for worker in self._workers:
            worker.join(timeout=2)

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
                while not self._pending_for_lane_locked(lane) and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                job = self._pick_next_job_locked(lane)
            try:
                value = job.execute()
                job.mark_done(value=value)
            except BaseException as err:  # noqa: BLE001 - pass through execution failures
                job.mark_done(error=err)

    def _pending_for_lane_locked(self, lane: str) -> List[Job]:
        return [job for job in self._pending if self._lane_for_job(job) == lane]

    def _pick_next_job_locked(self, lane: str) -> Job:
        lane_pending = self._pending_for_lane_locked(lane)
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
