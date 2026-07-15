from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.auth import redact_sensitive_text
from app.provider_contracts import (
    CancellationToken,
    ChatDelta,
    ChatRequest,
    ChatToolCall,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
    ModelContextProfile,
)


class OllamaChatProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 120.0,
        health_timeout_seconds: float = 10.0,
        opener=None,
        metrics=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.health_timeout_seconds = health_timeout_seconds
        self.opener = opener or urllib.request.urlopen
        self.metrics = metrics
        self._context_cache: dict[str, tuple[float, ModelContextProfile]] = {}

    def list_models(self) -> list[str]:
        try:
            with self.opener(f"{self.base_url}/api/tags", timeout=self.health_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return [item.get("name") for item in payload.get("models", []) if item.get("name")]
        except Exception:
            return []

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        models = self.list_models()
        elapsed = int((time.monotonic() - started) * 1000)
        if models:
            self._record("health", "ready", elapsed)
            return ProviderHealth(self.name, ProviderStatus.READY, "Ollama is reachable.", elapsed)
        self._record("health", "unavailable", elapsed)
        return ProviderHealth(
            self.name,
            ProviderStatus.UNAVAILABLE,
            "Ollama could not be reached or returned no installed models.",
            elapsed,
        )

    def model_context(self, model: str) -> ModelContextProfile:
        cached = self._context_cache.get(model)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        profile = ModelContextProfile(self.name, model, None, "unavailable")
        request = urllib.request.Request(
            f"{self.base_url}/api/show",
            data=json.dumps({"model": model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.health_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            lengths = []
            for key, value in (payload.get("model_info") or {}).items():
                if str(key).endswith(".context_length"):
                    try:
                        lengths.append(int(value))
                    except (TypeError, ValueError):
                        continue
            if lengths:
                profile = ModelContextProfile(self.name, model, max(lengths), "ollama_api_show")
        except Exception:
            pass
        self._context_cache[model] = (time.monotonic(), profile)
        return profile

    def generate(self, request: ChatRequest, cancellation: CancellationToken) -> str:
        chunks = []
        for delta in self.stream(request, cancellation):
            if delta.text:
                chunks.append(delta.text)
        return "".join(chunks)

    def stream(self, request: ChatRequest, cancellation: CancellationToken):
        started = time.monotonic()
        outcome = "failed"
        cancellation.raise_if_cancelled()
        request_payload = {
            "model": request.model,
            "messages": request.messages,
            "stream": True,
            **({"options": request.options} if request.options else {}),
            **({"tools": request.tools} if request.tools else {}),
            **({"format": request.response_format} if request.response_format else {}),
        }
        payload = json.dumps(request_payload).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
            method="POST",
        )
        response = None
        try:
            response = self.opener(http_request, timeout=request.timeout_seconds or self.timeout_seconds)
            cancellation.register(response.close)
            while True:
                cancellation.raise_if_cancelled()
                raw = response.readline()
                cancellation.raise_if_cancelled()
                if not raw:
                    break
                try:
                    frame = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProviderError(
                        provider=self.name,
                        code="invalid_stream",
                        user_message="The model provider returned an invalid streaming response.",
                        retryable=True,
                    ) from exc
                if frame.get("error"):
                    raise ProviderError(
                        provider=self.name,
                        code="stream_error",
                        user_message="The model provider failed while generating the response.",
                        retryable=True,
                    )
                message = frame.get("message") or {}
                text = message.get("content") or ""
                tool_calls = []
                for raw_call in message.get("tool_calls") or []:
                    function = raw_call.get("function") if isinstance(raw_call, dict) else None
                    if not isinstance(function, dict) or not function.get("name"):
                        continue
                    arguments = function.get("arguments") or {}
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError as exc:
                            raise ProviderError(
                                provider=self.name,
                                code="invalid_tool_call",
                                user_message="The model provider returned invalid capability arguments.",
                            ) from exc
                    if not isinstance(arguments, dict):
                        raise ProviderError(
                            provider=self.name,
                            code="invalid_tool_call",
                            user_message="The model provider returned invalid capability arguments.",
                        )
                    tool_calls.append(
                        ChatToolCall(
                            name=str(function["name"]),
                            arguments=arguments,
                            call_id=str(raw_call.get("id")) if raw_call.get("id") else None,
                        )
                    )
                done = bool(frame.get("done"))
                yield ChatDelta(
                    text=text,
                    done=done,
                    finish_reason=frame.get("done_reason"),
                    metadata={
                        key: frame[key]
                        for key in (
                            "total_duration",
                            "load_duration",
                            "prompt_eval_count",
                            "eval_count",
                        )
                        if key in frame
                    },
                    tool_calls=tool_calls,
                )
                if done:
                    outcome = "completed"
                    return
            if not cancellation.cancelled:
                raise ProviderError(
                    provider=self.name,
                    code="incomplete_stream",
                    user_message="The model provider closed the response before completion.",
                    retryable=True,
                )
        except ProviderError:
            outcome = "cancelled" if cancellation.cancelled else "failed"
            raise
        except TimeoutError as exc:
            raise ProviderError(
                provider=self.name,
                code="timeout",
                user_message="The model provider timed out.",
                retryable=True,
            ) from exc
        except urllib.error.HTTPError as exc:
            request_id = exc.headers.get("x-request-id") if exc.headers else None
            raise ProviderError(
                provider=self.name,
                code=f"http_{exc.code}",
                user_message="The model provider rejected the request.",
                retryable=exc.code >= 500 or exc.code == 429,
                request_id=redact_sensitive_text(request_id or "") or None,
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            if cancellation.cancelled:
                cancellation.raise_if_cancelled()
            raise ProviderError(
                provider=self.name,
                code="unavailable",
                user_message="The model provider is unavailable.",
                retryable=True,
            ) from exc
        finally:
            if response is not None:
                response.close()
            self._record("chat", outcome, int((time.monotonic() - started) * 1000))

    def _record(self, operation: str, status: str, latency_ms: int) -> None:
        if self.metrics:
            self.metrics.provider(self.name, operation, status, latency_ms)
