import unittest

from app.memory_guard import MemoryBackpressureError, MemoryGuard, MemorySnapshot
from app.model_residency import ModelResidencyManager


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class MemoryGuardTests(unittest.TestCase):
    def test_required_evictions_selects_oldest_models(self):
        guard = MemoryGuard(logger=_Logger(), snapshot_provider=lambda: MemorySnapshot(timestamp=0, vram_free_mb=0))
        manager = ModelResidencyManager(vram_budget_mb=1000)
        manager.ensure_loaded("image_local", 600, model_id="img")
        manager.ensure_loaded("llm", 300, model_id="chat")

        evictions = guard.required_evictions(
            {"task_type": "video", "model_id": "video:new", "estimated_vram_mb": 500},
            manager,
        )
        self.assertEqual(evictions[0], "image_local:img")

    def test_safe_offload_path_raises_when_no_ram_or_disk_headroom(self):
        guard = MemoryGuard(
            logger=_Logger(),
            snapshot_provider=lambda: MemorySnapshot(
                timestamp=0,
                ram_total_mb=16000,
                ram_used_mb=15900,
                ram_free_mb=100,
                swap_total_mb=1000,
                swap_used_mb=950,
                swap_free_mb=50,
                swap_pressure=0.95,
            ),
        )
        guard._snapshot = MemorySnapshot(
            timestamp=0,
            ram_total_mb=16000,
            ram_used_mb=15900,
            ram_free_mb=100,
            swap_total_mb=1000,
            swap_used_mb=950,
            swap_free_mb=50,
            swap_pressure=0.95,
        )
        guard._disk_free_mb = lambda _path: 50  # type: ignore[method-assign]
        with self.assertRaises(MemoryBackpressureError):
            guard.safe_offload_path("llm:tiny", 400)


if __name__ == "__main__":
    unittest.main()
