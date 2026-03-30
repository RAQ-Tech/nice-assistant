import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class MemorySnapshot:
    timestamp: float
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    ram_free_mb: int = 0
    swap_total_mb: int = 0
    swap_used_mb: int = 0
    swap_free_mb: int = 0
    swap_pressure: float = 0.0
    vram_total_mb: int = 0
    vram_used_mb: int = 0
    vram_free_mb: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "ram": {
                "total_mb": self.ram_total_mb,
                "used_mb": self.ram_used_mb,
                "free_mb": self.ram_free_mb,
            },
            "swap": {
                "total_mb": self.swap_total_mb,
                "used_mb": self.swap_used_mb,
                "free_mb": self.swap_free_mb,
                "pressure": round(self.swap_pressure, 4),
            },
            "vram": {
                "total_mb": self.vram_total_mb,
                "used_mb": self.vram_used_mb,
                "free_mb": self.vram_free_mb,
            },
        }


class MemoryBackpressureError(RuntimeError):
    def __init__(self, user_message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.details = details or {}


class MemoryGuard:
    def __init__(
        self,
        *,
        logger,
        poll_interval_seconds: float = 2.0,
        swap_pressure_threshold: float = 0.85,
        snapshot_provider: Optional[Callable[[], MemorySnapshot]] = None,
    ):
        self.logger = logger
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds or 2.0))
        self.swap_pressure_threshold = max(0.0, min(1.0, float(swap_pressure_threshold or 0.85)))
        self._snapshot_provider = snapshot_provider or self._collect_snapshot
        self._lock = threading.Lock()
        self._snapshot = MemorySnapshot(timestamp=time.time())
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="memory-guard-poller", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def snapshot(self) -> MemorySnapshot:
        with self._lock:
            return self._snapshot

    def can_admit(self, job: Dict[str, Any], residency_manager) -> bool:
        snap = self.snapshot()
        required_mb = max(0, int(job.get("estimated_vram_mb") or 0))
        used_budget_mb = residency_manager.estimated_gpu_used_mb()
        projected_used = used_budget_mb + required_mb
        budget_ok = residency_manager.vram_budget_mb <= 0 or projected_used <= residency_manager.vram_budget_mb
        vram_ok = snap.vram_free_mb <= 0 or required_mb <= snap.vram_free_mb
        swap_ok = snap.swap_total_mb <= 0 or snap.swap_pressure < self.swap_pressure_threshold
        return bool(budget_ok and vram_ok and swap_ok)

    def required_evictions(self, job: Dict[str, Any], residency_manager) -> List[str]:
        required_mb = max(0, int(job.get("estimated_vram_mb") or 0))
        loaded = residency_manager.loaded_gpu_models()
        candidates = [m for m in loaded if not m["model_id"].startswith(f"{job.get('task_type')}:")]
        candidates.sort(key=lambda item: item.get("last_request_at_monotonic", 0.0))
        used_budget = residency_manager.estimated_gpu_used_mb()
        need_to_free = max(0, (used_budget + required_mb) - max(0, int(residency_manager.vram_budget_mb or 0)))
        if need_to_free <= 0:
            return []
        selected: List[str] = []
        freed = 0
        for item in candidates:
            selected.append(item["model_id"])
            freed += max(0, int(item.get("estimated_vram_mb") or 0))
            if freed >= need_to_free:
                break
        return selected

    def safe_offload_path(self, model: str, target_memory: int) -> str:
        snap = self.snapshot()
        required_mb = max(0, int(target_memory or 0))
        if snap.ram_free_mb >= required_mb and (
            snap.swap_total_mb <= 0 or snap.swap_pressure < self.swap_pressure_threshold
        ):
            return "cpu"
        disk_free_mb = self._disk_free_mb("/")
        if disk_free_mb > required_mb:
            return "disk"
        raise MemoryBackpressureError(
            "The server is temporarily at memory capacity and cannot safely offload models right now. Please retry shortly.",
            details={"model": model, "required_mb": required_mb},
        )

    def preflight_load_or_raise(self, *, task_type: str, model_id: str, estimated_vram_mb: int, residency_manager):
        job = {"task_type": task_type, "model_id": model_id, "estimated_vram_mb": estimated_vram_mb}
        snap = self.snapshot()
        can_admit = self.can_admit(job, residency_manager)
        evictions = self.required_evictions(job, residency_manager)
        action = "admit" if can_admit else "backpressure"
        self._log_decision(
            decision=action,
            action="preflight_load",
            model=model_id,
            task_type=task_type,
            estimated_vram_mb=estimated_vram_mb,
            required_evictions=evictions,
            memory_snapshot=snap.as_dict(),
        )
        if not can_admit and not evictions:
            raise MemoryBackpressureError(
                "I’m at GPU memory capacity right now. Please wait for active jobs to finish and retry.",
                details={"task_type": task_type, "model_id": model_id, "estimated_vram_mb": estimated_vram_mb},
            )

    def preflight_offload_target_or_raise(self, *, model_id: str, target_memory_mb: int) -> str:
        chosen_target = self.safe_offload_path(model_id, target_memory_mb)
        self._log_decision(
            decision="admit",
            action="preflight_offload",
            model=model_id,
            task_type=model_id.split(":", 1)[0],
            estimated_vram_mb=target_memory_mb,
            required_evictions=[],
            memory_snapshot=self.snapshot().as_dict(),
            chosen_action=chosen_target,
        )
        return chosen_target

    def _log_decision(self, **payload):
        self.logger.info("memory.guard %s", json.dumps(payload, sort_keys=True))

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                snap = self._snapshot_provider()
                with self._lock:
                    self._snapshot = snap
            except Exception as exc:  # noqa: BLE001 - telemetry should not fail app runtime
                self.logger.warning("memory.guard polling failed: %s", exc)
            self._stop_event.wait(self.poll_interval_seconds)

    def _collect_snapshot(self) -> MemorySnapshot:
        ram = self._system_ram_snapshot()
        vram = self._vram_snapshot()
        return MemorySnapshot(
            timestamp=time.time(),
            ram_total_mb=ram["total_mb"],
            ram_used_mb=ram["used_mb"],
            ram_free_mb=ram["free_mb"],
            swap_total_mb=ram["swap_total_mb"],
            swap_used_mb=ram["swap_used_mb"],
            swap_free_mb=ram["swap_free_mb"],
            swap_pressure=ram["swap_pressure"],
            vram_total_mb=vram["total_mb"],
            vram_used_mb=vram["used_mb"],
            vram_free_mb=vram["free_mb"],
        )

    def _system_ram_snapshot(self) -> Dict[str, Any]:
        try:
            import psutil  # type: ignore

            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            return {
                "total_mb": int(vm.total // (1024 * 1024)),
                "used_mb": int((vm.total - vm.available) // (1024 * 1024)),
                "free_mb": int(vm.available // (1024 * 1024)),
                "swap_total_mb": int(sm.total // (1024 * 1024)),
                "swap_used_mb": int(sm.used // (1024 * 1024)),
                "swap_free_mb": int((sm.total - sm.used) // (1024 * 1024)),
                "swap_pressure": (float(sm.used) / float(sm.total)) if sm.total else 0.0,
            }
        except Exception:
            meminfo = self._read_meminfo()
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
            swap_total = meminfo.get("SwapTotal", 0)
            swap_free = meminfo.get("SwapFree", 0)
            swap_used = max(0, swap_total - swap_free)
            return {
                "total_mb": total // 1024,
                "used_mb": max(0, (total - available) // 1024),
                "free_mb": available // 1024,
                "swap_total_mb": swap_total // 1024,
                "swap_used_mb": swap_used // 1024,
                "swap_free_mb": swap_free // 1024,
                "swap_pressure": (float(swap_used) / float(swap_total)) if swap_total else 0.0,
            }

    @staticmethod
    def _read_meminfo() -> Dict[str, int]:
        vals: Dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    raw = rest.strip().split(" ", 1)[0]
                    try:
                        vals[key] = int(raw)
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        return vals

    def _vram_snapshot(self) -> Dict[str, int]:
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return {"total_mb": 0, "used_mb": 0, "free_mb": 0}
        cmd = [
            nvidia_smi,
            "--query-gpu=memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
        try:
            output = subprocess.check_output(cmd, timeout=1.5, text=True).strip()
            total = used = free = 0
            for line in output.splitlines():
                cols = [x.strip() for x in line.split(",")]
                if len(cols) < 3:
                    continue
                total += int(float(cols[0]))
                used += int(float(cols[1]))
                free += int(float(cols[2]))
            return {"total_mb": total, "used_mb": used, "free_mb": free}
        except Exception:
            return {"total_mb": 0, "used_mb": 0, "free_mb": 0}

    @staticmethod
    def _disk_free_mb(path: str) -> int:
        try:
            usage = shutil.disk_usage(path)
            return int(usage.free // (1024 * 1024))
        except Exception:
            return 0
