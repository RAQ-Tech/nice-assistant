import unittest
from unittest import mock

from app.model_residency import (
    ModelResidencyManager,
    ResidencyPolicy,
    STATE_LOADED_ON_CPU,
)


class ModelResidencyPolicyTests(unittest.TestCase):
    def test_idle_hold_delays_eviction_for_image_models(self):
        manager = ModelResidencyManager(
            vram_budget_mb=1024,
            policy=ResidencyPolicy(gpu_idle_hold_seconds_image=30),
        )
        with mock.patch("app.model_residency.time.monotonic", side_effect=[100.0, 105.0]):
            manager.ensure_loaded("image_local", 700, model_id="sdxl")
            manager.ensure_loaded("llm", 700, model_id="tiny")
        self.assertEqual(manager._records["image_local:sdxl"].state, "loaded_on_gpu")

    def test_min_residency_prevents_immediate_unload(self):
        manager = ModelResidencyManager(
            vram_budget_mb=1024,
            policy=ResidencyPolicy(gpu_min_residency_seconds=20),
        )
        with mock.patch("app.model_residency.time.monotonic", side_effect=[100.0, 110.0]):
            manager.ensure_loaded("image_local", 700, model_id="sdxl")
            manager.ensure_loaded("llm", 700, model_id="tiny")
        self.assertEqual(manager._records["image_local:sdxl"].state, "loaded_on_gpu")

    def test_affinity_window_uses_recent_history_to_delay_eviction(self):
        manager = ModelResidencyManager(
            vram_budget_mb=1024,
            policy=ResidencyPolicy(queue_affinity_window_ms=30000),
        )
        with mock.patch("app.model_residency.time.monotonic", side_effect=[100.0, 115.0, 118.0]):
            manager.ensure_loaded("image_local", 300, model_id="sdxl")
            manager.ensure_loaded("image_local", 300, model_id="sdxl")
            manager.ensure_loaded("llm", 800, model_id="tiny")
        self.assertEqual(manager._records["image_local:sdxl"].state, "loaded_on_gpu")

    def test_swap_limit_throttles_evictions(self):
        manager = ModelResidencyManager(
            vram_budget_mb=1000,
            policy=ResidencyPolicy(max_model_swaps_per_minute=1),
        )
        with mock.patch("app.model_residency.time.monotonic", side_effect=[100.0, 101.0, 102.0]):
            manager.ensure_loaded("image_local", 600, model_id="img")
            manager.ensure_loaded("video", 300, model_id="vid")
            manager.ensure_loaded("llm", 600, model_id="llm")
        self.assertEqual(manager._records["image_local:img"].state, STATE_LOADED_ON_CPU)
        self.assertEqual(manager._records["video:vid"].state, "loaded_on_gpu")


if __name__ == "__main__":
    unittest.main()
