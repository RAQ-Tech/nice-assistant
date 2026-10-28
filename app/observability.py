from __future__ import annotations

from collections import Counter, deque
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import secrets
import threading
import time
import traceback

from app.auth import redact_sensitive_text


request_id_context: ContextVar[str] = ContextVar("nice_assistant_request_id", default="")

# How many recent timings each conversation stage keeps for its percentiles.
STAGE_SAMPLES = 256


def new_request_id() -> str:
    return secrets.token_hex(12)


def exception_location(exc_info) -> str:
    """Where an exception was raised, as file:line, and nothing else.

    The class name alone left an operator with no way to find the fault. A
    full traceback would carry the exception's message and every path on the
    way, which is more than a log should hold, so this is the innermost frame
    by file name only - enough to open the right file at the right line.
    """

    frames = traceback.extract_tb(exc_info[2]) if exc_info and exc_info[2] else []
    if not frames:
        return ""
    frame = frames[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"


class RedactedJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact_sensitive_text(record.getMessage())
        if record.exc_info:
            message = f"{message} exception={record.exc_info[0].__name__}"
            location = exception_location(record.exc_info)
            if location:
                message = f"{message} at {location}"
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
        self._stage_latency_ms: dict[str, Counter] = {}
        self._stage_samples: dict[str, deque] = {}
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

    def stage(self, name: str, latency_ms: int) -> None:
        """One timed step between somebody speaking and hearing the answer.

        Kept apart from the provider totals, which sum every operation
        together: the question these answer is "how long does the first word
        take", and an average over everything cannot answer it.
        """

        value = max(0, int(latency_ms))
        with self._lock:
            totals = self._stage_latency_ms.setdefault(name, Counter())
            totals["count"] += 1
            totals["sum"] += value
            totals["max"] = max(totals["max"], value)
            self._stage_samples.setdefault(name, deque(maxlen=STAGE_SAMPLES)).append(value)

    def snapshot(self) -> dict:
        with self._lock:
            requests = dict(sorted(self._request_counts.items()))
            request_latency = dict(self._request_latency_ms)
            jobs = dict(sorted(self._job_counts.items()))
            job_latency = dict(self._job_latency_ms)
            providers = dict(sorted(self._provider_counts.items()))
            provider_latency = dict(self._provider_latency_ms)
            stages = {
                name: {
                    **_latency_response(dict(totals)),
                    "recent_p50": _percentile(self._stage_samples[name], 50),
                    "recent_p95": _percentile(self._stage_samples[name], 95),
                }
                for name, totals in sorted(self._stage_latency_ms.items())
            }
        return {
            "started_at": self.started_at,
            "uptime_seconds": max(0, int(self.clock() - self.started_monotonic)),
            "requests": {"counts": requests, "latency_ms": _latency_response(request_latency)},
            "jobs": {"counts": jobs, "latency_ms": _latency_response(job_latency)},
            "providers": {"counts": providers, "latency_ms": _latency_response(provider_latency)},
            "stages": stages,
        }


def _percentile(samples, percent: int) -> int | None:
    ordered = sorted(samples)
    if not ordered:
        return None
    rank = max(1, -(-len(ordered) * percent // 100))
    return ordered[rank - 1]


def _latency_response(values: dict) -> dict:
    count = int(values.get("count", 0))
    total = int(values.get("sum", 0))
    return {
        "count": count,
        "average": round(total / count, 2) if count else None,
        "max": int(values.get("max", 0)) if count else None,
    }
