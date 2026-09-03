"""One reply's speech, spoken a sentence at a time and stored once.

Speaking while a reply is still being written means asking the provider for
each finished sentence as it appears (ADR 0042). Those requests belong
together: they are one reply, and replay should play one recording of it, not
a stitched set of files that cache rotation could pull apart. A session is
that belonging. It keeps what each piece produced, in order, and becomes a
recording only when the browser says the reply is finished. A session nobody
finishes - the person stopped it, or walked away - stores nothing, exactly as
an abandoned stream stores nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import threading
import time

from app.service_errors import NotFoundError

# Long enough for any reply to be written and spoken; short enough that a
# browser that vanished mid-reply does not pin its bytes in memory for long.
SESSION_TTL_SECONDS = 600


@dataclass
class SpeechSession:
    id: str
    user_id: str
    plan: dict
    created_at: float
    collected: list = field(default_factory=list)
    abandoned: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class SpeechSessionRegistry:
    def __init__(self, clock=time.monotonic, ttl_seconds: float = SESSION_TTL_SECONDS):
        self._clock = clock
        self._ttl = ttl_seconds
        self._sessions: dict[str, SpeechSession] = {}
        self._lock = threading.Lock()

    def begin(self, user_id: str, plan: dict) -> SpeechSession:
        session = SpeechSession(id=secrets.token_hex(8), user_id=user_id, plan=plan, created_at=self._clock())
        with self._lock:
            self._prune()
            self._sessions[session.id] = session
        return session

    def get(self, user_id: str, session_id: str) -> SpeechSession:
        with self._lock:
            session = self._sessions.get(session_id)
        # Another person's session is not found rather than forbidden: its id
        # says nothing about whose it is, and neither should the answer.
        if session is None or session.user_id != user_id:
            raise NotFoundError("speech session not found")
        return session

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _prune(self) -> None:
        cutoff = self._clock() - self._ttl
        for session_id, session in list(self._sessions.items()):
            if session.created_at < cutoff:
                session.abandoned = True
                del self._sessions[session_id]
