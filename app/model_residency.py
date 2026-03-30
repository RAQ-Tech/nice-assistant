import logging
import threading
from dataclasses import dataclass
from typing import Dict, Optional


logger = logging.getLogger("nice-assistant")

STATE_LOADED_ON_GPU = "loaded_on_gpu"
STATE_LOADED_ON_CPU = "loaded_on_cpu"
STATE_UNLOADED = "unloaded"
STATE_LOADING = "loading"
STATE_EVICTING = "evicting"


@dataclass
class ModelResidencyRecord:
    model_id: str
    state: str = STATE_UNLOADED
    estimated_vram_mb: int = 0


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
    def __init__(self, vram_budget_mb: int = 0):
        self.vram_budget_mb = max(0, int(vram_budget_mb or 0))
        self._lock = threading.RLock()
        self._adapters: Dict[str, ResidencyAdapter] = {}
        self._records: Dict[str, ModelResidencyRecord] = {}

    def register_adapter(self, task_type: str, adapter: ResidencyAdapter):
        with self._lock:
            self._adapters[task_type] = adapter

    def ensure_loaded(self, task_type: str, estimated_vram_mb: int, model_id: Optional[str] = None):
        model_key = self._model_key(task_type, model_id)
        with self._lock:
            self.maybe_evict_for(task_type, estimated_vram_mb)
            adapter = self._adapters.get(task_type)
            record = self._records.setdefault(model_key, ModelResidencyRecord(model_id=model_key))
            record.state = STATE_LOADING
            record.estimated_vram_mb = max(0, int(estimated_vram_mb or 0))
            if adapter:
                record.state = adapter.ensure_loaded(model_key, record.estimated_vram_mb)
            else:
                record.state = STATE_LOADED_ON_GPU
            return record.state

    def maybe_evict_for(self, task_type: str, estimated_vram_mb: int = 0):
        with self._lock:
            if self.vram_budget_mb <= 0:
                return
            need = max(0, int(estimated_vram_mb or 0))
            used = sum(
                r.estimated_vram_mb
                for r in self._records.values()
                if r.state == STATE_LOADED_ON_GPU
            )
            if used + need <= self.vram_budget_mb:
                return
            for model_id, record in self._records.items():
                if not model_id.startswith(f"{task_type}:") and record.state == STATE_LOADED_ON_GPU:
                    self.offload(model_id, target="cpu")
                    used -= record.estimated_vram_mb
                    if used + need <= self.vram_budget_mb:
                        break

    def offload(self, model_id: str, target: str = "cpu"):
        with self._lock:
            record = self._records.get(model_id)
            if not record:
                return STATE_UNLOADED
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

    @staticmethod
    def _model_key(task_type: str, model_id: Optional[str]):
        suffix = (model_id or "default").strip() or "default"
        return f"{task_type}:{suffix}"


def build_default_residency_manager(vram_budget_mb: int = 0) -> ModelResidencyManager:
    manager = ModelResidencyManager(vram_budget_mb=vram_budget_mb)
    manager.register_adapter("llm", LLMBackendAdapter("ollama_or_local"))
    manager.register_adapter("image_local", LocalImageBackendAdapter("automatic1111_or_comfyui"))
    logger.info("model residency manager initialized vram_budget_mb=%s", manager.vram_budget_mb)
    return manager
