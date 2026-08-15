"""The per-persona scene backlog.

A record of pictures that have been proposed for a persona but not made. It
exists separately from the retained library because "we could make this" and "we
have this" are different facts, and conflating them would make a plan look like
an achievement.

Nothing generates from the backlog yet. Producing these is separate work, and
recording proposals first means the ideas can be reviewed before any GPU time is
spent on them.
"""

from __future__ import annotations

import json

from app.media_scene import normalize_scene, scene_is_empty, scene_summary
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError


BACKLOG_STATES = ("proposed", "approved", "generating", "done", "retired")
BACKLOG_SOURCES = ("operator", "persona_card", "lorebook", "conversation")
# Transitions an operator can ask for. Generation states are set by the work
# itself, not by someone clicking, so they are deliberately absent.
OPERATOR_TRANSITIONS = {
    "proposed": {"approved", "retired"},
    "approved": {"proposed", "retired"},
    "generating": set(),
    "done": {"retired"},
    "retired": {"proposed"},
}
MAX_BACKLOG_ENTRIES = 500


class SceneBacklogService:
    def __init__(self, session_factory, secret_store, logger):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.logger = logger

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def entries(self, user_id: str, *, persona_id: str | None = None, state: str | None = None) -> list[dict]:
        if state and state not in BACKLOG_STATES:
            raise RequestError("unknown backlog state", 400)
        with self._uow() as uow:
            rows = uow.repo.scene_backlog_entries(user_id, persona_id=persona_id, state=state)
            return [self._public(row) for row in rows]

    def propose(
        self,
        user_id: str,
        *,
        persona_id: str,
        scene,
        source: str = "operator",
        source_detail: str = "",
    ) -> dict:
        if source not in BACKLOG_SOURCES:
            raise RequestError("unknown backlog source", 400)
        scene = normalize_scene(scene)
        if scene_is_empty(scene):
            raise RequestError("describe the picture being proposed", 400)
        with self._uow() as uow:
            if not uow.repo.persona_for_user(user_id, persona_id):
                raise NotFoundError("persona not found")
            if uow.repo.scene_backlog_count(user_id) >= MAX_BACKLOG_ENTRIES:
                raise RequestError(
                    f"the scene backlog is full at {MAX_BACKLOG_ENTRIES} entries; retire some before adding more",
                    409,
                )
            row = uow.repo.add_scene_backlog_entry(
                user_id=user_id,
                persona_id=persona_id,
                scene_json=json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
                source=source,
                source_detail=str(source_detail or "")[:500],
            )
            return self._public(row)

    def set_state(self, user_id: str, entry_id: str, state: str) -> dict:
        if state not in BACKLOG_STATES:
            raise RequestError("unknown backlog state", 400)
        with self._uow() as uow:
            row = uow.repo.scene_backlog_entry(user_id, entry_id)
            if not row:
                raise NotFoundError("backlog entry not found")
            if state not in OPERATOR_TRANSITIONS.get(row.state, set()):
                raise RequestError(
                    f"a {row.state} scene cannot be moved to {state}",
                    409,
                )
            row.state = state
            row.updated_at = now_ts()
            return self._public(row)

    def remove(self, user_id: str, entry_id: str) -> bool:
        with self._uow() as uow:
            row = uow.repo.scene_backlog_entry(user_id, entry_id)
            if not row:
                return False
            uow.session.delete(row)
            return True

    @staticmethod
    def _public(row) -> dict:
        try:
            scene = json.loads(row.scene_json or "{}")
        except (TypeError, ValueError):
            scene = {}
        return {
            "id": row.id,
            "persona_id": row.persona_id,
            "scene": scene,
            "summary": scene_summary(scene),
            "state": row.state,
            "source": row.source,
            "source_detail": row.source_detail or "",
            "media_id": row.media_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
