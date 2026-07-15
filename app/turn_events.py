from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
import time


TERMINAL_EVENTS = {"turn.completed", "turn.failed", "turn.cancelled"}


@dataclass(frozen=True)
class TurnEvent:
    sequence: int
    event: str
    data: dict


class _TurnStream:
    def __init__(self, max_events: int, max_bytes: int):
        self.condition = threading.Condition()
        self.events: deque[TurnEvent] = deque()
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.bytes = 0
        self.next_sequence = 1
        self.terminal_at: float | None = None
        self.accumulated_text = ""

    def publish(self, event: str, data: dict) -> TurnEvent:
        with self.condition:
            item = TurnEvent(self.next_sequence, event, data)
            self.next_sequence += 1
            self.events.append(item)
            if event == "assistant.delta":
                self.accumulated_text += str(data.get("text") or "")
            self.bytes += len(json.dumps(data, separators=(",", ":"), default=str).encode("utf-8"))
            while len(self.events) > self.max_events or self.bytes > self.max_bytes:
                removed = self.events.popleft()
                self.bytes -= len(json.dumps(removed.data, separators=(",", ":"), default=str).encode("utf-8"))
            if event in TERMINAL_EVENTS:
                self.terminal_at = time.monotonic()
            self.condition.notify_all()
            return item


class TurnEventBroker:
    def __init__(self, max_events: int = 4096, max_bytes: int = 2 * 1024 * 1024, retention_seconds: int = 300):
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.retention_seconds = retention_seconds
        self._streams: dict[str, _TurnStream] = {}
        self._lock = threading.Lock()
        self._stopped = False

    def _stream(self, turn_id: str) -> _TurnStream:
        with self._lock:
            self._prune_locked()
            return self._streams.setdefault(turn_id, _TurnStream(self.max_events, self.max_bytes))

    def publish(self, turn_id: str, event: str, data: dict) -> TurnEvent:
        if self._stopped:
            return TurnEvent(0, event, data)
        return self._stream(turn_id).publish(event, data)

    def subscribe(self, turn_id: str, snapshot: dict, last_event_id: int | None = None):
        stream = self._stream(turn_id)
        cursor = max(0, int(last_event_id or 0))
        yield TurnEvent(0, "turn.snapshot", snapshot)
        with stream.condition:
            replay_available = any(event.sequence > cursor for event in stream.events)
        if snapshot.get("status") in {"completed", "failed", "cancelled"} and not replay_available:
            return
        while not self._stopped:
            heartbeat = False
            with stream.condition:
                available = [event for event in stream.events if event.sequence > cursor]
                if not available:
                    if stream.terminal_at is not None:
                        return
                    stream.condition.wait(timeout=15)
                    available = [event for event in stream.events if event.sequence > cursor]
                    if not available:
                        heartbeat = True
            if heartbeat:
                yield None
                continue
            for event in available:
                cursor = event.sequence
                yield event
                if event.event in TERMINAL_EVENTS:
                    return

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            streams = list(self._streams.values())
        for stream in streams:
            with stream.condition:
                stream.condition.notify_all()

    def accumulated_text(self, turn_id: str) -> str:
        stream = self._stream(turn_id)
        with stream.condition:
            return stream.accumulated_text

    def replace_accumulated_text(self, turn_id: str, text: str) -> None:
        stream = self._stream(turn_id)
        with stream.condition:
            stream.accumulated_text = text

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            turn_id
            for turn_id, stream in self._streams.items()
            if stream.terminal_at is not None and now - stream.terminal_at >= self.retention_seconds
        ]
        for turn_id in expired:
            self._streams.pop(turn_id, None)
