import json
import unittest
from unittest import mock

from app.provider_contracts import CapacityStatus
from app.resource_providers import (
    Automatic1111ResourceProvider,
    ComfyUIResourceProvider,
    OllamaResourceProvider,
)


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"" if self.payload is None else json.dumps(self.payload).encode()


class ResourceProviderTests(unittest.TestCase):
    def test_comfyui_parses_device_and_queue_telemetry_and_releases(self):
        requests = []

        def respond(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/system_stats"):
                return FakeResponse(
                    {"devices": [{"name": "RTX", "type": "cuda", "vram_total": 12 * 1024**3, "vram_free": 5 * 1024**3}]}
                )
            if request.full_url.endswith("/queue"):
                return FakeResponse({"queue_running": [[1]], "queue_pending": [[2], [3]]})
            return FakeResponse()

        provider = ComfyUIResourceProvider(3)
        with mock.patch("app.resource_providers.urllib.request.urlopen", side_effect=respond):
            snapshot = provider.snapshot("http://comfy:8188", "name:password")
            released = provider.release("http://comfy:8188", "name:password")

        self.assertEqual(snapshot.status, CapacityStatus.KNOWN)
        self.assertEqual(snapshot.total_vram_mb, 12288)
        self.assertEqual(snapshot.free_vram_mb, 5120)
        self.assertEqual(snapshot.active_jobs, 1)
        self.assertEqual(snapshot.queue_depth, 2)
        release_request = requests[-1][0]
        self.assertTrue(release_request.full_url.endswith("/free"))
        self.assertEqual(json.loads(release_request.data), {"unload_models": True, "free_memory": True})
        self.assertEqual(released["scope"], "cached_models")
        self.assertTrue(release_request.headers.get("Authorization", "").startswith("Basic "))

    def test_automatic1111_parses_memory_and_uses_coarse_checkpoint_release(self):
        requests = []

        def respond(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/memory"):
                return FakeResponse({"cuda": {"system": {"total": 12 * 1024**3, "free": 4 * 1024**3}}})
            return FakeResponse()

        provider = Automatic1111ResourceProvider(3)
        with mock.patch("app.resource_providers.urllib.request.urlopen", side_effect=respond):
            snapshot = provider.snapshot("http://a1111:7860")
            released = provider.release("http://a1111:7860")

        self.assertEqual(snapshot.status, CapacityStatus.KNOWN)
        self.assertEqual(snapshot.free_vram_mb, 4096)
        self.assertTrue(requests[-1][0].full_url.endswith("/sdapi/v1/unload-checkpoint"))
        self.assertEqual(released["scope"], "active_checkpoint")

    def test_ollama_reports_loaded_vram_and_unloads_each_named_model(self):
        requests = []

        def respond(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/api/ps"):
                return FakeResponse(
                    {
                        "models": [
                            {"model": "chat-model", "size_vram": 3 * 1024**3},
                            {"name": "task-model", "size_vram": 2 * 1024**3},
                        ]
                    }
                )
            return FakeResponse({"done": True})

        provider = OllamaResourceProvider(3)
        with mock.patch("app.resource_providers.urllib.request.urlopen", side_effect=respond):
            snapshot = provider.snapshot("http://ollama:11434")
            released = provider.release("http://ollama:11434")

        self.assertEqual(snapshot.status, CapacityStatus.UNKNOWN)
        self.assertEqual([item["vram_mb"] for item in snapshot.loaded_models], [3072, 2048])
        payloads = [json.loads(request.data) for request, _timeout in requests if request.data]
        self.assertEqual({item["model"] for item in payloads}, {"chat-model", "task-model"})
        self.assertTrue(all(item["keep_alive"] == 0 and item["stream"] is False for item in payloads))
        self.assertEqual(released["model_count"], 2)

    def test_provider_failures_return_unavailable_without_exposing_exception_text(self):
        provider = ComfyUIResourceProvider(1)
        with mock.patch("app.resource_providers.urllib.request.urlopen", side_effect=RuntimeError("secret URL")):
            snapshot = provider.snapshot("http://comfy:8188")
        self.assertEqual(snapshot.status, CapacityStatus.UNAVAILABLE)
        self.assertNotIn("secret", snapshot.message.lower())


if __name__ == "__main__":
    unittest.main()
