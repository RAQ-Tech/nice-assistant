"""The retained picture library, and serving from it.

A picture that already exists arrives instantly. A better picture that takes
forty seconds arrives after the conversation has moved on, so reuse is worth
real design rather than being treated as a cache.

Matching is over the scene record, not prompt text. Two prompts can differ
completely and describe the same picture, and comparing rendered strings would
either miss that or produce false matches nobody can explain. Scene fields are
comparable because they are separate fields.
"""

from __future__ import annotations

import json

from app.media_scene import SCENE_FIELDS, normalize_scene, scene_is_empty
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError


# A subject that does not match is a different picture. The rest describe the
# same subject in different circumstances, so they weigh less.
SUBJECT_WEIGHT = 3
FIELD_WEIGHT = 1
MATCH_THRESHOLD = 5
DEFAULT_LIBRARY_LIMIT = 200


def _tokens(value: str) -> set[str]:
    return {word for word in str(value or "").casefold().replace(",", " ").split() if len(word) > 2}


def scene_similarity(wanted: dict, stored: dict) -> int:
    """Score how well a retained picture answers a request.

    Deliberately crude and explainable: shared words per field, weighted so the
    subject dominates. A wrong subject can never be rescued by a matching
    setting.
    """

    if scene_is_empty(wanted) or scene_is_empty(stored):
        return 0
    subject_overlap = _tokens(wanted.get("subject", "")) & _tokens(stored.get("subject", ""))
    if not subject_overlap:
        return 0
    score = SUBJECT_WEIGHT * len(subject_overlap)
    for field in SCENE_FIELDS:
        if field == "subject":
            continue
        wanted_value = wanted.get(field, "")
        stored_value = stored.get(field, "")
        if not wanted_value:
            continue
        if not stored_value:
            # The request asked for something the stored picture says nothing
            # about, which is a reason to doubt the match rather than ignore it.
            score -= FIELD_WEIGHT
            continue
        score += FIELD_WEIGHT * len(_tokens(wanted_value) & _tokens(stored_value))
    return score


class MediaLibraryService:
    def __init__(self, session_factory, secret_store, logger, entry_limit: int = DEFAULT_LIBRARY_LIMIT):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.logger = logger
        self.entry_limit = max(0, int(entry_limit))

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    # -- retention ---------------------------------------------------------

    def retain(self, user_id: str, *, media_id: str, persona_id: str | None, scene, origin_chat_id=None) -> str:
        """Keep a generated picture, with the scene that produced it.

        A picture with no scene is not retained: without one there is nothing to
        match a later request against, and a library of unmatchable images is
        just disk use.
        """

        scene = normalize_scene(scene)
        if scene_is_empty(scene):
            return ""
        try:
            with self._uow() as uow:
                row = uow.repo.add_library_entry(
                    user_id=user_id,
                    persona_id=persona_id,
                    media_id=media_id,
                    scene_json=json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
                    origin_chat_id=origin_chat_id,
                )
                self._enforce_limit(uow.repo, user_id)
                return row.id if row else ""
        except Exception:
            # Retention is an optimisation. Losing it must never cost the
            # picture the owner actually asked for.
            if self.logger:
                self.logger.warning("library entry could not be retained")
            return ""

    def _enforce_limit(self, repo, user_id: str) -> None:
        if not self.entry_limit:
            return
        repo.retire_oldest_library_entries(user_id, keep=self.entry_limit)

    # -- serving -----------------------------------------------------------

    def find_ready(self, user_id: str, *, persona_id: str | None, scene, chat_id: str | None) -> dict | None:
        """Return the best ready picture for this request, or nothing.

        Never returns a picture already served into this conversation: the same
        image arriving twice reads as a mistake, however well it matches.
        """

        wanted = normalize_scene(scene)
        if scene_is_empty(wanted):
            return None
        with self._uow() as uow:
            rows = uow.repo.library_entries(user_id, persona_id=persona_id, state="ready")
            best = None
            best_score = 0
            for row in rows:
                if chat_id and chat_id in {row.last_served_chat_id, row.origin_chat_id}:
                    # Neither re-send a picture this conversation already saw,
                    # nor hand back one it just asked to have made.
                    continue
                stored = normalize_scene(json.loads(row.scene_json or "{}"))
                score = scene_similarity(wanted, stored)
                if score > best_score:
                    best, best_score = row, score
            if not best or best_score < MATCH_THRESHOLD:
                return None
            return {"id": best.id, "media_id": best.media_id, "score": best_score}

    def mark_served(self, user_id: str, entry_id: str, chat_id: str | None) -> None:
        try:
            with self._uow() as uow:
                row = uow.repo.library_entry(user_id, entry_id)
                if not row:
                    return
                row.state = "served"
                row.served_count += 1
                row.last_served_chat_id = chat_id
                row.last_served_at = now_ts()
        except Exception:
            if self.logger:
                self.logger.warning("library entry could not be marked served")

    # -- operator surface --------------------------------------------------

    def entries(self, user_id: str, *, persona_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._uow() as uow:
            rows = uow.repo.library_entries(user_id, persona_id=persona_id, limit=limit)
            return [self._public(row) for row in rows]

    def add_existing(self, user_id: str, *, media_id: str, persona_id: str | None, scene) -> dict:
        scene = normalize_scene(scene)
        if scene_is_empty(scene):
            raise RequestError("describe the picture before adding it, so it can be matched later", 400)
        with self._uow() as uow:
            if not uow.repo.media_for_user(user_id, media_id):
                raise NotFoundError("media not found")
            row = uow.repo.add_library_entry(
                user_id=user_id,
                persona_id=persona_id,
                media_id=media_id,
                scene_json=json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
            )
            if not row:
                raise RequestError("that picture is already in the library", 409)
            self._enforce_limit(uow.repo, user_id)
            return self._public(row)

    def remove(self, user_id: str, entry_id: str) -> bool:
        with self._uow() as uow:
            row = uow.repo.library_entry(user_id, entry_id)
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
            "media_id": row.media_id,
            "content_url": f"/api/v1/media/{row.media_id}",
            "scene": scene,
            "state": row.state,
            "served_count": row.served_count,
            "created_at": row.created_at,
            "last_served_at": row.last_served_at,
        }
