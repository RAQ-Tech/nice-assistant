import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from app.memory_guard import MemoryBackpressureError


logger = logging.getLogger("nice-assistant")

STATE_LOADED_ON_GPU = "loaded_on_gpu"
STATE_LOADED_ON_CPU = "loaded_on_cpu"
STATE_UNLOADED = "unloaded"
STATE_LOADING = "loading"
STATE_EVICTING = "evicting"


@dataclass
class ResidencyPolicy:
    gpu_idle_hold_seconds_llm: float = 0.0
    gpu_idle_hold_seconds_image: float = 0.0
    gpu_min_residency_seconds: float = 0.0
    max_model_swaps_per_minute: int = 60
    queue_affinity_window_ms: int = 0


@dataclass
class ModelResidencyRecord:
    model_id: str
    state: str = STATE_UNLOADED
    estimated_vram_mb: int = 0
    loaded_at_monotonic: float = 0.0
    last_request_at_monotonic: float = 0.0


class ResidencyAdapter:
    """Base adapter for backend-specific residency operations."""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def ensure_loaded(self, model_id: str, estimated_vram_mb: int) -> str:
        return STATE_LOADED_ON_GPU

    def offload(self, model_id: str, target: str = "cpu") -> str:
        return STATE_LOADED_ON_CPU if target == "cpu" else STATE_UNLOADED

    def unload(self, model_id: str) -> str:
        return STATE_UNLOADED


class LLMBackendAdapter(ResidencyAdapter):
    """Adapter for Ollama / local LLM runner style backends."""


class LocalImageBackendAdapter(ResidencyAdapter):
    """Adapter for Automatic1111 / ComfyUI style local image backends."""


class ModelResidencyManager:
    def __init__(self, vram_budget_mb: int = 0, policy: Optional[ResidencyPolicy] = None, memory_guard=None):
        self.vram_budget_mb = max(0, int(vram_budget_mb or 0))
        self.policy = policy or ResidencyPolicy()
        self.memory_guard = memory_guard
        self._lock = threading.RLock()
        self._adapters: Dict[str, ResidencyAdapter] = {}
        self._records: Dict[str, ModelResidencyRecord] = {}
        self._request_history: Dict[str, Deque[float]] = {}
        self._swap_events: Deque[float] = deque()

    def register_adapter(self, task_type: str, adapter: ResidencyAdapter):
        with self._lock:
            self._adapters[task_type] = adapter

    def update_policy(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                if value is None or not hasattr(self.policy, key):
                    continue
                setattr(self.policy, key, self._normalize_policy_value(key, value))

    def ensure_loaded(self, task_type: str, estimated_vram_mb: int, model_id: Optional[str] = None):
        model_key = self._model_key(task_type, model_id)
        now = time.monotonic()
        with self._lock:
            self._record_request(task_type, now)
            if self.memory_guard:
                self.memory_guard.preflight_load_or_raise(
                    task_type=task_type,
                    model_id=model_key,
                    estimated_vram_mb=estimated_vram_mb,
                    residency_manager=self,
                )
            self.maybe_evict_for(task_type, estimated_vram_mb, now_monotonic=now)
            if self.memory_guard and not self.memory_guard.can_admit(
                {"task_type": task_type, "model_id": model_key, "estimated_vram_mb": estimated_vram_mb},
                self,
            ):
                raise MemoryBackpressureError(
                    "I’m still waiting for enough GPU memory to become available. Please retry in a moment.",
                    details={"task_type": task_type, "model_id": model_key, "estimated_vram_mb": estimated_vram_mb},
                )
            adapter = self._adapters.get(task_type)
            record = self._records.setdefault(model_key, ModelResidencyRecord(model_id=model_key))
            was_loaded = record.state == STATE_LOADED_ON_GPU
            record.state = STATE_LOADING
            record.estimated_vram_mb = max(0, int(estimated_vram_mb or 0))
            if adapter:
                record.state = adapter.ensure_loaded(model_key, record.estimated_vram_mb)
            else:
                record.state = STATE_LOADED_ON_GPU
            if record.state == STATE_LOADED_ON_GPU and not was_loaded:
                record.loaded_at_monotonic = now
            record.last_request_at_monotonic = now
            return record.state

    def maybe_evict_for(self, task_type: str, estimated_vram_mb: int = 0, now_monotonic: Optional[float] = None):
        with self._lock:
            if self.vram_budget_mb <= 0:
                return
            now = now_monotonic if now_monotonic is not None else time.monotonic()
            need = max(0, int(estimated_vram_mb or 0))
            used = sum(
                r.estimated_vram_mb
                for r in self._records.values()
                if r.state == STATE_LOADED_ON_GPU
            )
            if used + need <= self.vram_budget_mb:
                return
            for model_id, record in self._records.items():
                if model_id.startswith(f"{task_type}:") or record.state != STATE_LOADED_ON_GPU:
                    continue
                candidate_task_type = model_id.split(":", 1)[0]
                if self._should_delay_eviction(record, candidate_task_type, now):
                    continue
                if self._swap_limit_reached(now):
                    break
                self.offload(model_id, target="cpu")
                used -= record.estimated_vram_mb
                self._swap_events.append(now)
                if used + need <= self.vram_budget_mb:
                    break

    def offload(self, model_id: str, target: str = "cpu"):
        with self._lock:
            record = self._records.get(model_id)
            if not record:
                return STATE_UNLOADED
            if self.memory_guard:
                target = self.memory_guard.preflight_offload_target_or_raise(
                    model_id=model_id,
                    target_memory_mb=record.estimated_vram_mb,
                )
            record.state = STATE_EVICTING
            task_type = model_id.split(":", 1)[0]
            adapter = self._adapters.get(task_type)
            if adapter:
                record.state = adapter.offload(model_id, target=target)
            else:
                record.state = STATE_LOADED_ON_CPU if target == "cpu" else STATE_UNLOADED
            return record.state

    def unload(self, model_id: str):
        with self._lock:
            record = self._records.get(model_id)
            if not record:
                return STATE_UNLOADED
            record.state = STATE_EVICTING
            task_type = model_id.split(":", 1)[0]
            adapter = self._adapters.get(task_type)
            if adapter:
                record.state = adapter.unload(model_id)
            else:
                record.state = STATE_UNLOADED
            return record.state

    def _should_delay_eviction(self, record: ModelResidencyRecord, task_type: str, now_monotonic: float) -> bool:
        hold_seconds = self._idle_hold_seconds(task_type)
        if hold_seconds > 0 and record.last_request_at_monotonic and (now_monotonic - record.last_request_at_monotonic) < hold_seconds:
            return True
        min_residency = max(0.0, float(self.policy.gpu_min_residency_seconds or 0.0))
        if min_residency > 0 and record.loaded_at_monotonic and (now_monotonic - record.loaded_at_monotonic) < min_residency:
            return True
        if self._is_affinity_likely(task_type, now_monotonic):
            return True
        return False

    def _idle_hold_seconds(self, task_type: str) -> float:
        if task_type == "llm":
            return max(0.0, float(self.policy.gpu_idle_hold_seconds_llm or 0.0))
        if task_type.startswith("image"):
            return max(0.0, float(self.policy.gpu_idle_hold_seconds_image or 0.0))
        return 0.0

    def _record_request(self, task_type: str, now_monotonic: float):
        history = self._request_history.setdefault(task_type, deque())
        history.append(now_monotonic)
        self._trim_history(history, now_monotonic)

    def _trim_history(self, history: Deque[float], now_monotonic: float):
        affinity_seconds = max(0.0, (int(self.policy.queue_affinity_window_ms or 0) / 1000.0))
        hold_window = max(
            affinity_seconds,
            float(self.policy.gpu_idle_hold_seconds_llm or 0.0),
            float(self.policy.gpu_idle_hold_seconds_image or 0.0),
            60.0,
        )
        threshold = now_monotonic - hold_window
        while history and history[0] < threshold:
            history.popleft()

    def _is_affinity_likely(self, task_type: str, now_monotonic: float) -> bool:
        window_ms = max(0, int(self.policy.queue_affinity_window_ms or 0))
        if window_ms <= 0:
            return False
        history = self._request_history.get(task_type)
        if not history:
            return False
        window_seconds = window_ms / 1000.0
        recent = [ts for ts in history if now_monotonic - ts <= window_seconds]
        return len(recent) >= 2

    def _swap_limit_reached(self, now_monotonic: float) -> bool:
        max_swaps = int(self.policy.max_model_swaps_per_minute or 0)
        if max_swaps <= 0:
            return False
        threshold = now_monotonic - 60.0
        while self._swap_events and self._swap_events[0] < threshold:
            self._swap_events.popleft()
        return len(self._swap_events) >= max_swaps

    @staticmethod
    def _normalize_policy_value(key: str, value):
        if key in {"gpu_idle_hold_seconds_llm", "gpu_idle_hold_seconds_image", "gpu_min_residency_seconds"}:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return 0.0
        if key in {"max_model_swaps_per_minute", "queue_affinity_window_ms"}:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0
        return value

    @staticmethod
    def _model_key(task_type: str, model_id: Optional[str]):
        suffix = (model_id or "default").strip() or "default"
        return f"{task_type}:{suffix}"

    def loaded_gpu_models(self):
        with self._lock:
            return [
                {
                    "model_id": model_id,
                    "estimated_vram_mb": rec.estimated_vram_mb,
                    "last_request_at_monotonic": rec.last_request_at_monotonic,
                }
                for model_id, rec in self._records.items()
                if rec.state == STATE_LOADED_ON_GPU
            ]

    def estimated_gpu_used_mb(self) -> int:
        with self._lock:
            return sum(rec.estimated_vram_mb for rec in self._records.values() if rec.state == STATE_LOADED_ON_GPU)


def build_default_residency_manager(
    vram_budget_mb: int = 0,
    policy: Optional[ResidencyPolicy] = None,
    memory_guard=None,
) -> ModelResidencyManager:
    manager = ModelResidencyManager(vram_budget_mb=vram_budget_mb, policy=policy, memory_guard=memory_guard)
    manager.register_adapter("llm", LLMBackendAdapter("ollama_or_local"))
    manager.register_adapter("image_local", LocalImageBackendAdapter("automatic1111_or_comfyui"))
    logger.info("model residency manager initialized vram_budget_mb=%s", manager.vram_budget_mb)
    return manager
