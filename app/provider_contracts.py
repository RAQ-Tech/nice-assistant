from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
import time
from typing import Iterable, Protocol


class ProviderStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    status: ProviderStatus
    message: str
    latency_ms: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ProviderStatus.READY


class ProviderError(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        code: str,
        user_message: str,
        retryable: bool = False,
        request_id: str | None = None,
    ):
        super().__init__(user_message)
        self.provider = provider
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.request_id = request_id

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.user_message,
            "provider": self.provider,
            "retryable": self.retryable,
            "request_id": self.request_id,
        }


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()
        self._callbacks: list = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        callbacks = []
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def register(self, callback) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ProviderError(
                provider="application",
                code="cancelled",
                user_message="Request cancelled.",
            )


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[dict]
    options: dict = field(default_factory=dict)
    tools: list[dict] = field(default_factory=list)
    response_format: dict | str | None = None
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class ChatToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    call_id: str | None = None


@dataclass(frozen=True)
class ChatDelta:
    text: str = ""
    done: bool = False
    finish_reason: str | None = None
    metadata: dict = field(default_factory=dict)
    tool_calls: list[ChatToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ModelContextProfile:
    provider: str
    model: str
    max_context_tokens: int | None
    source: str


@dataclass(frozen=True)
class MediaRequest:
    kind: str
    prompt: str
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MediaArtifact:
    kind: str
    content: bytes
    extension: str
    content_type: str


class CapacityStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderRuntimeCapabilities:
    provider: str
    reports_capacity: bool
    reports_queue: bool
    supports_release: bool
    supports_precise_cancel: bool = False


@dataclass(frozen=True)
class ProviderCapacitySnapshot:
    provider: str
    status: CapacityStatus
    source: str
    observed_at: float = field(default_factory=time.time)
    total_vram_mb: int | None = None
    free_vram_mb: int | None = None
    queue_depth: int | None = None
    active_jobs: int | None = None
    loaded_models: tuple[dict, ...] = ()
    message: str = ""


class ResourceControlProvider(Protocol):
    name: str

    def capabilities(self, endpoint: str, api_auth: str | None = None) -> ProviderRuntimeCapabilities: ...

    def snapshot(self, endpoint: str, api_auth: str | None = None) -> ProviderCapacitySnapshot: ...

    def release(self, endpoint: str, api_auth: str | None = None) -> dict: ...


class ChatModelProvider(Protocol):
    name: str

    def list_models(self) -> list[str]: ...

    def health(self) -> ProviderHealth: ...

    def model_context(self, model: str) -> ModelContextProfile: ...

    def stream(self, request: ChatRequest, cancellation: CancellationToken) -> Iterable[ChatDelta]: ...

    def generate(self, request: ChatRequest, cancellation: CancellationToken) -> str: ...


class MediaProvider(Protocol):
    name: str

    def health(self) -> ProviderHealth: ...

    def generate(self, request: MediaRequest, cancellation: CancellationToken) -> MediaArtifact: ...
