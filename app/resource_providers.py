from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from app.provider_contracts import (
    CapacityStatus,
    ProviderCapacitySnapshot,
    ProviderRuntimeCapabilities,
)


def _auth_headers(value: str | None) -> dict[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    encoded = base64.b64encode(raw.encode()).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _request_json(
    url: str,
    *,
    timeout: float,
    api_auth: str | None = None,
    payload: dict | None = None,
) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {**_auth_headers(api_auth)}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read()
    if not content:
        return {}
    parsed = json.loads(content.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("provider returned a non-object response")
    return parsed


def _mb(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return max(0, int(value) // (1024 * 1024))
    except (TypeError, ValueError):
        return None


class ComfyUIResourceProvider:
    name = "comfyui"

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def capabilities(self, endpoint: str, api_auth: str | None = None) -> ProviderRuntimeCapabilities:
        # /free is a core route on supported ComfyUI versions. A release call is
        # still verified with a fresh /system_stats sample before it is trusted.
        return ProviderRuntimeCapabilities(self.name, True, True, True, False)

    def snapshot(self, endpoint: str, api_auth: str | None = None) -> ProviderCapacitySnapshot:
        base = endpoint.rstrip("/")
        try:
            stats = _request_json(f"{base}/system_stats", timeout=self.timeout_seconds, api_auth=api_auth)
            devices = stats.get("devices") if isinstance(stats.get("devices"), list) else []
            device = next((item for item in devices if isinstance(item, dict) and item.get("type") != "cpu"), None)
            queue_depth = None
            active_jobs = None
            try:
                queue = _request_json(f"{base}/queue", timeout=self.timeout_seconds, api_auth=api_auth)
                running = queue.get("queue_running") if isinstance(queue.get("queue_running"), list) else []
                pending = queue.get("queue_pending") if isinstance(queue.get("queue_pending"), list) else []
                active_jobs = len(running)
                queue_depth = len(pending)
            except Exception:
                pass
            total = _mb(device.get("vram_total")) if device else None
            free = _mb(device.get("vram_free")) if device else None
            status = CapacityStatus.KNOWN if free is not None else CapacityStatus.UNKNOWN
            return ProviderCapacitySnapshot(
                self.name,
                status,
                "/system_stats",
                total_vram_mb=total,
                free_vram_mb=free,
                queue_depth=queue_depth,
                active_jobs=active_jobs,
                message="ComfyUI did not report GPU capacity." if free is None else "",
            )
        except Exception:
            return ProviderCapacitySnapshot(
                self.name,
                CapacityStatus.UNAVAILABLE,
                "/system_stats",
                message="ComfyUI capacity telemetry is unavailable.",
            )

    def release(self, endpoint: str, api_auth: str | None = None) -> dict:
        _request_json(
            f"{endpoint.rstrip('/')}/free",
            timeout=self.timeout_seconds,
            api_auth=api_auth,
            payload={"unload_models": True, "free_memory": True},
        )
        return {"requested": True, "scope": "cached_models"}


class Automatic1111ResourceProvider:
    name = "automatic1111"

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def capabilities(self, endpoint: str, api_auth: str | None = None) -> ProviderRuntimeCapabilities:
        return ProviderRuntimeCapabilities(self.name, True, False, True, False)

    def snapshot(self, endpoint: str, api_auth: str | None = None) -> ProviderCapacitySnapshot:
        try:
            payload = _request_json(
                f"{endpoint.rstrip('/')}/sdapi/v1/memory",
                timeout=self.timeout_seconds,
                api_auth=api_auth,
            )
            cuda = payload.get("cuda") if isinstance(payload.get("cuda"), dict) else {}
            system = cuda.get("system") if isinstance(cuda.get("system"), dict) else {}
            total = _mb(system.get("total"))
            free = _mb(system.get("free"))
            status = CapacityStatus.KNOWN if free is not None else CapacityStatus.UNKNOWN
            return ProviderCapacitySnapshot(
                self.name,
                status,
                "/sdapi/v1/memory",
                total_vram_mb=total,
                free_vram_mb=free,
                message="Automatic1111 did not report GPU capacity." if free is None else "",
            )
        except Exception:
            return ProviderCapacitySnapshot(
                self.name,
                CapacityStatus.UNAVAILABLE,
                "/sdapi/v1/memory",
                message="Automatic1111 capacity telemetry is unavailable.",
            )

    def release(self, endpoint: str, api_auth: str | None = None) -> dict:
        _request_json(
            f"{endpoint.rstrip('/')}/sdapi/v1/unload-checkpoint",
            timeout=self.timeout_seconds,
            api_auth=api_auth,
            payload={},
        )
        return {"requested": True, "scope": "active_checkpoint"}


class OllamaResourceProvider:
    name = "ollama"

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def capabilities(self, endpoint: str, api_auth: str | None = None) -> ProviderRuntimeCapabilities:
        return ProviderRuntimeCapabilities(self.name, False, False, True, False)

    def snapshot(self, endpoint: str, api_auth: str | None = None) -> ProviderCapacitySnapshot:
        try:
            payload = _request_json(f"{endpoint.rstrip('/')}/api/ps", timeout=self.timeout_seconds)
            values = payload.get("models") if isinstance(payload.get("models"), list) else []
            loaded = []
            for value in values:
                if not isinstance(value, dict):
                    continue
                name = str(value.get("model") or value.get("name") or "").strip()
                loaded.append({"name": name, "vram_mb": _mb(value.get("size_vram"))})
            return ProviderCapacitySnapshot(
                self.name,
                CapacityStatus.UNKNOWN,
                "/api/ps",
                loaded_models=tuple(loaded),
                message="Ollama reports loaded-model use, not total free GPU capacity.",
            )
        except Exception:
            return ProviderCapacitySnapshot(
                self.name,
                CapacityStatus.UNAVAILABLE,
                "/api/ps",
                message="Ollama runtime telemetry is unavailable.",
            )

    def release(self, endpoint: str, api_auth: str | None = None) -> dict:
        snapshot = self.snapshot(endpoint)
        if snapshot.status is CapacityStatus.UNAVAILABLE:
            raise RuntimeError("Ollama runtime telemetry is unavailable")
        released = []
        for item in snapshot.loaded_models:
            model = str(item.get("name") or "").strip()
            if not model:
                continue
            _request_json(
                f"{endpoint.rstrip('/')}/api/generate",
                timeout=self.timeout_seconds,
                payload={"model": model, "keep_alive": 0, "stream": False},
            )
            released.append(model)
        return {"requested": True, "scope": "loaded_models", "model_count": len(released)}
