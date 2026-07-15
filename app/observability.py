from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import secrets
import threading
import time

from app.auth import redact_sensitive_text


request_id_context: ContextVar[str] = ContextVar("nice_assistant_request_id", default="")


def new_request_id() -> str:
    return secrets.token_hex(12)


class RedactedJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact_sensitive_text(record.getMessage())
        if record.exc_info:
            message = f"{message} exception={record.exc_info[0].__name__}"
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "thread": record.threadName,
            "request_id": request_id_context.get() or None,
            "message": message,
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


class MetricsRegistry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started_at = int(time.time())
        self.started_monotonic = clock()
        self._request_counts = Counter()
        self._request_latency_ms = Counter()
        self._job_counts = Counter()
        self._job_latency_ms = Counter()
        self._provider_counts = Counter()
        self._provider_latency_ms = Counter()
        self._lock = threading.Lock()

    def request(self, method: str, status: int, latency_ms: int) -> None:
        key = f"{method.upper()}:{int(status)}"
        with self._lock:
            self._request_counts[key] += 1
            self._request_latency_ms["count"] += 1
            self._request_latency_ms["sum"] += max(0, int(latency_ms))
            self._request_latency_ms["max"] = max(self._request_latency_ms["max"], int(latency_ms))

    def job(self, kind: str, status: str, latency_ms: int) -> None:
        key = f"{str(kind or 'unknown')}:{status}"
        with self._lock:
            self._job_counts[key] += 1
            self._job_latency_ms["count"] += 1
            self._job_latency_ms["sum"] += max(0, int(latency_ms))
            self._job_latency_ms["max"] = max(self._job_latency_ms["max"], int(latency_ms))

    def provider(self, provider: str, operation: str, status: str, latency_ms: int) -> None:
        key = f"{provider}:{operation}:{status}"
        with self._lock:
            self._provider_counts[key] += 1
            self._provider_latency_ms["count"] += 1
            self._provider_latency_ms["sum"] += max(0, int(latency_ms))
            self._provider_latency_ms["max"] = max(self._provider_latency_ms["max"], int(latency_ms))

    def snapshot(self) -> dict:
        with self._lock:
            requests = dict(sorted(self._request_counts.items()))
            request_latency = dict(self._request_latency_ms)
            jobs = dict(sorted(self._job_counts.items()))
            job_latency = dict(self._job_latency_ms)
            providers = dict(sorted(self._provider_counts.items()))
            provider_latency = dict(self._provider_latency_ms)
        return {
            "started_at": self.started_at,
            "uptime_seconds": max(0, int(self.clock() - self.started_monotonic)),
            "requests": {"counts": requests, "latency_ms": _latency_response(request_latency)},
            "jobs": {"counts": jobs, "latency_ms": _latency_response(job_latency)},
            "providers": {"counts": providers, "latency_ms": _latency_response(provider_latency)},
        }


def _latency_response(values: dict) -> dict:
    count = int(values.get("count", 0))
    total = int(values.get("sum", 0))
    return {
        "count": count,
        "average": round(total / count, 2) if count else None,
        "max": int(values.get("max", 0)) if count else None,
    }
