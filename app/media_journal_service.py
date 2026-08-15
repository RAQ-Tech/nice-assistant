"""Durable per-generation journals.

Recording is deliberately defensive. A journal is diagnostic evidence, so a
failure to write one must never fail the generation it is describing - the
operator would lose the picture *and* the explanation. Every write is guarded
and logged instead.
"""

from __future__ import annotations

from contextlib import contextmanager
import time

from app.media_journal import (
    JOURNAL_ORIGINS,
    MAX_STAGES,
    STAGE_STATUSES,
    decode_detail,
    encode_detail,
    render_export,
)
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError


class _NullRecorder:
    """Stands in when no journal could be opened, so callers need no branches."""

    id = ""

    def record(self, stage, *, summary="", detail=None, status="ok", duration_ms=None):
        return None

    @contextmanager
    def timed(self, stage, *, summary="", detail=None):
        yield _StageHandle()

    def attach_media(self, media_id):
        return None

    def finish(self, status, *, media_id=None, error_code=None, error_message=None):
        return None


NULL_JOURNAL = _NullRecorder()


class _StageHandle:
    """Lets a `timed` block enrich its stage after the work has run."""

    def __init__(self):
        self.summary = ""
        self.detail = {}
        self.status = "ok"

    def set(self, *, summary=None, detail=None, status=None):
        if summary is not None:
            self.summary = summary
        if detail is not None:
            self.detail = detail
        if status is not None:
            self.status = status


class JournalRecorder:
    def __init__(self, service: "MediaJournalService", journal_id: str):
        self._service = service
        self.id = journal_id

    def record(self, stage, *, summary="", detail=None, status="ok", duration_ms=None):
        self._service._record_stage(
            self.id,
            stage=stage,
            summary=summary,
            detail=detail,
            status=status,
            duration_ms=duration_ms,
        )

    @contextmanager
    def timed(self, stage, *, summary="", detail=None):
        handle = _StageHandle()
        handle.summary = summary
        handle.detail = detail or {}
        started = time.monotonic()
        try:
            yield handle
        except BaseException as exc:
            self._service._record_stage(
                self.id,
                stage=stage,
                summary=handle.summary or f"{type(exc).__name__} raised",
                detail=handle.detail,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        self._service._record_stage(
            self.id,
            stage=stage,
            summary=handle.summary,
            detail=handle.detail,
            status=handle.status,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def attach_media(self, media_id):
        self._service._attach_media(self.id, media_id)

    def finish(self, status, *, media_id=None, error_code=None, error_message=None):
        self._service._finish(
            self.id,
            status,
            media_id=media_id,
            error_code=error_code,
            error_message=error_message,
        )


class MediaJournalService:
    def __init__(self, session_factory, secret_store, logger, retention_days: int = 60):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.logger = logger
        self.retention_days = max(0, int(retention_days))

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def _warn(self, message: str, exc: BaseException) -> None:
        if self.logger:
            self.logger.warning("media journal %s: %s", message, type(exc).__name__)

    # -- recording ---------------------------------------------------------

    def start(
        self,
        *,
        user_id: str,
        kind: str,
        origin: str,
        chat_id: str | None = None,
        persona_id: str | None = None,
        media_plan_id: str | None = None,
        capability_request_id: str | None = None,
    ):
        if origin not in JOURNAL_ORIGINS:
            origin = "direct"
        try:
            with self._uow() as uow:
                row = uow.repo.add_media_generation_journal(
                    user_id=user_id,
                    chat_id=chat_id,
                    persona_id=persona_id,
                    media_plan_id=media_plan_id,
                    capability_request_id=capability_request_id,
                    kind=kind,
                    origin=origin,
                )
                return JournalRecorder(self, row.id)
        except Exception as exc:
            self._warn("could not be opened", exc)
            return _NullRecorder()

    def _record_stage(self, journal_id, *, stage, summary, detail, status, duration_ms):
        if status not in STAGE_STATUSES:
            status = "ok"
        try:
            with self._uow() as uow:
                if uow.repo.media_generation_journal_stage_count(journal_id) >= MAX_STAGES:
                    return
                uow.repo.add_media_generation_journal_stage(
                    journal_id=journal_id,
                    stage=str(stage)[:120],
                    status=status,
                    summary=str(summary or "")[:1000],
                    detail_json=encode_detail(detail),
                    started_at=now_ts(),
                    duration_ms=None if duration_ms is None else max(0, int(duration_ms)),
                )
        except Exception as exc:
            self._warn("stage could not be recorded", exc)

    def _attach_media(self, journal_id, media_id):
        try:
            with self._uow() as uow:
                row = uow.repo.media_generation_journal_by_id(journal_id)
                if row:
                    row.media_id = media_id
        except Exception as exc:
            self._warn("media could not be attached", exc)

    def _finish(self, journal_id, status, *, media_id=None, error_code=None, error_message=None):
        try:
            with self._uow() as uow:
                row = uow.repo.media_generation_journal_by_id(journal_id)
                if not row or row.status != "running":
                    return
                row.status = status
                if media_id:
                    row.media_id = media_id
                row.error_code = str(error_code)[:120] if error_code else None
                row.error_message = str(error_message)[:500] if error_message else None
                row.completed_at = now_ts()
                row.duration_ms = max(0, (row.completed_at - row.started_at) * 1000)
        except Exception as exc:
            self._warn("could not be finished", exc)

    # -- reading -----------------------------------------------------------

    @staticmethod
    def _public(row, stages) -> dict:
        return {
            "id": row.id,
            "kind": row.kind,
            "origin": row.origin,
            "status": row.status,
            "chat_id": row.chat_id,
            "persona_id": row.persona_id,
            "media_id": row.media_id,
            "media_plan_id": row.media_plan_id,
            "capability_request_id": row.capability_request_id,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "duration_ms": row.duration_ms,
            "error": (
                {"code": row.error_code or "failed", "message": row.error_message or ""}
                if (row.error_code or row.error_message)
                else None
            ),
            "stages": [
                {
                    "sequence": stage.sequence,
                    "stage": stage.stage,
                    "status": stage.status,
                    "summary": stage.summary,
                    "detail": decode_detail(stage.detail_json),
                    "started_at": stage.started_at,
                    "duration_ms": stage.duration_ms,
                }
                for stage in stages
            ],
        }

    def journal(self, user_id: str, journal_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.media_generation_journal_by_id(journal_id)
            if not row or row.user_id != user_id:
                raise NotFoundError()
            return self._public(row, uow.repo.media_generation_journal_stages(row.id))

    def journal_for_media(self, user_id: str, media_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.media_generation_journal_for_media(user_id, media_id)
            if not row:
                raise NotFoundError()
            return self._public(row, uow.repo.media_generation_journal_stages(row.id))

    def journals(self, user_id: str, *, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._uow() as uow:
            rows = uow.repo.media_generation_journals(user_id, limit=limit, offset=offset)
            return [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "origin": row.origin,
                    "status": row.status,
                    "media_id": row.media_id,
                    "started_at": row.started_at,
                    "duration_ms": row.duration_ms,
                    "stage_count": uow.repo.media_generation_journal_stage_count(row.id),
                }
                for row in rows
            ]

    def export(self, user_id: str, journal_id: str) -> tuple[str, str]:
        journal = self.journal(user_id, journal_id)
        return f"generation-journal-{journal['id']}.md", render_export(journal)

    def purge(self, user_id: str, retention_days: int | None = None) -> int:
        days = self.retention_days if retention_days is None else max(0, int(retention_days))
        if not days:
            return 0
        cutoff = now_ts() - days * 86_400
        try:
            with self._uow() as uow:
                return uow.repo.delete_media_generation_journals_before(user_id, cutoff)
        except Exception as exc:
            self._warn("retention pass failed", exc)
            return 0
