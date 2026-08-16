"""Making a photo set, as a set rather than as several pictures.

Every frame of a set is generated from the same shared scene and the same
preset, and its seed follows from the set's base seed. That is what stops
wardrobe, room, and lighting drifting between frames the way they do when the
same idea is described several times and generated separately.

Frames are chat-less capability requests queued as bulk work, exactly like a
background picture: the operator asked for the set, nobody is waiting on any
individual frame, and a picture somebody actually requested must always be
chosen first.
"""

from __future__ import annotations

import json
import secrets

from app.media_scene import scene_summary
from app.photo_set import frame_scene, frame_seed, normalize_definition, set_state, shared_summary
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError


MAX_SETS = 200


class PhotoSetService:
    def __init__(self, session_factory, secret_store, logger, capabilities=None, jobs=None):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.logger = logger
        self.capabilities = capabilities
        self.jobs = jobs

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    # -- authoring ---------------------------------------------------------

    def create(self, user_id: str, *, persona_id: str, scene, variations) -> dict:
        definition = normalize_definition({"scene": scene, "variations": variations})
        if definition["reasons"]:
            raise RequestError("; ".join(definition["reasons"]), 400)
        with self._uow() as uow:
            if not uow.repo.persona_for_user(user_id, persona_id):
                raise NotFoundError("persona not found")
            if len(uow.repo.photo_sets(user_id)) >= MAX_SETS:
                raise RequestError(f"the photo set list is full at {MAX_SETS}; retire some before adding more", 409)
            row = uow.repo.add_photo_set(
                user_id=user_id,
                persona_id=persona_id,
                scene_json=json.dumps(definition["scene"], separators=(",", ":"), ensure_ascii=False),
                variations_json=json.dumps(definition["variations"], separators=(",", ":"), ensure_ascii=False),
                # Chosen once, here, so the whole set is reproducible from it.
                base_seed=secrets.randbelow(2**31 - definition["frame_count"] - 1) + 1,
                frame_count=definition["frame_count"],
            )
            return self._public(uow.repo, row)

    def sets(self, user_id: str, *, persona_id: str | None = None) -> list[dict]:
        with self._uow() as uow:
            return [self._public(uow.repo, row) for row in uow.repo.photo_sets(user_id, persona_id=persona_id)]

    def get(self, user_id: str, set_id: str) -> dict | None:
        with self._uow() as uow:
            row = uow.repo.photo_set(user_id, set_id)
            return self._public(uow.repo, row) if row else None

    def remove(self, user_id: str, set_id: str) -> bool:
        with self._uow() as uow:
            row = uow.repo.photo_set(user_id, set_id)
            if not row:
                return False
            if row.state == "generating":
                raise RequestError("this set is being generated; wait for it to finish", 409)
            uow.session.delete(row)
            return True

    # -- producing ---------------------------------------------------------

    def produce(self, user_id: str, set_id: str) -> dict:
        """Queue every frame of a set, from one resolved plan.

        The frames are submitted together rather than one at a time because
        they share a preset: submitting them as a group is what makes "one
        plan" true rather than merely intended.
        """

        if not self.capabilities:
            raise RequestError("image capabilities are unavailable", 503)
        submissions = self._claim(user_id, set_id)
        started = []
        for frame_index, request_id, job_id in submissions:
            try:
                self.capabilities.submit_background(
                    user_id,
                    request_id,
                    job_id,
                    on_settled=self._settlement(user_id, set_id, request_id, frame_index),
                )
            except Exception:
                if self.logger:
                    self.logger.warning("photo set frame could not be submitted")
                continue
            started.append({"frame_index": frame_index, "request_id": request_id, "job_id": job_id})
        if not started:
            self._finish(user_id, set_id)
            raise RequestError("no frame of this set could be planned", 409)
        return {"set_id": set_id, "started": started}

    def _claim(self, user_id: str, set_id: str) -> list[tuple[int, str, str]]:
        """Move the set to generating and plan each frame, in one transaction."""

        with self._uow() as uow:
            row = uow.repo.photo_set(user_id, set_id)
            if not row:
                raise NotFoundError("photo set not found")
            if row.state == "generating":
                raise RequestError("this set is already being generated", 409)
            if row.state == "done":
                raise RequestError("this set is already made", 409)
            scene = _json(row.scene_json, {})
            variations = _json(row.variations_json, [])
            submissions = []
            for index, variation in enumerate(variations):
                composed = frame_scene(scene, variation)
                prepared = self.capabilities.prepare_background_request(
                    uow.repo,
                    user_id=user_id,
                    persona_id=row.persona_id,
                    scene=composed,
                    prompt=scene_summary(composed),
                    entry_id=f"{row.id}:{index}",
                    seed=frame_seed(row.base_seed, index),
                    photo_set_id=row.id,
                    frame_index=index,
                )
                if not prepared:
                    continue
                request_id, job_id = prepared
                submissions.append((index, request_id, job_id))
            row.state = "generating"
            row.updated_at = now_ts()
            return submissions

    def _settlement(self, user_id: str, set_id: str, request_id: str, frame_index: int):
        """Record a finished frame, and say honestly what the set now is."""

        def settle(repo, status: str, media_id: str) -> None:
            row = repo.photo_set(user_id, set_id)
            if not row:
                return
            if status == "completed" and media_id:
                entry = repo.library_entry_for_media(user_id, media_id)
                if entry:
                    entry.photo_set_id = set_id
                    entry.frame_index = frame_index
            frames_done = len(repo.photo_set_frames(user_id, set_id))
            outstanding = repo.unsettled_photo_set_requests(user_id, set_id, exclude=request_id)
            row.state = set_state(row.frame_count, frames_done, finished=not outstanding)
            row.updated_at = now_ts()

        return settle

    def _finish(self, user_id: str, set_id: str) -> None:
        with self._uow() as uow:
            row = uow.repo.photo_set(user_id, set_id)
            if not row or row.state != "generating":
                return
            frames_done = len(uow.repo.photo_set_frames(user_id, set_id))
            row.state = set_state(row.frame_count, frames_done, finished=True)
            row.updated_at = now_ts()

    # -- representation ----------------------------------------------------

    def _public(self, repo, row) -> dict:
        scene = _json(row.scene_json, {})
        frames = repo.photo_set_frames(row.user_id, row.id)
        return {
            "id": row.id,
            "persona_id": row.persona_id,
            "scene": scene,
            "shared": shared_summary(scene),
            "variations": _json(row.variations_json, []),
            "state": row.state,
            "base_seed": row.base_seed,
            "frame_count": row.frame_count,
            # Stated rather than implied: a set that made four of six frames is
            # neither finished nor still working.
            "frames_done": len(frames),
            "frames_missing": max(0, row.frame_count - len(frames)),
            "frames": [
                {
                    "frame_index": entry.frame_index,
                    "media_id": entry.media_id,
                    "content_url": f"/api/v1/media/{entry.media_id}",
                    "seed": frame_seed(row.base_seed, entry.frame_index or 0),
                }
                for entry in frames
            ],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def _json(value, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default
