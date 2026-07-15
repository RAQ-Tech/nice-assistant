from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy.exc import IntegrityError

from app.auth import redact_sensitive_text
from app.job_service import JobExecution, JobService
from app.provider_contracts import ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.task_contracts import MEMORY_EXTRACTION, MemoryExtractionTaskInput


EXTRACTOR_VERSION = "memory-candidates-task-v2"
MEMORY_STATUSES = {"pending", "active", "rejected", "forgotten", "superseded"}
SEARCH_STOP_WORDS = {
    "about",
    "and",
    "are",
    "but",
    "for",
    "from",
    "have",
    "how",
    "that",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "who",
    "with",
    "you",
    "your",
}
SENSITIVE_MEMORY_PATTERN = re.compile(
    r"(?i)\b(?:api[\s_-]?key|access token|refresh token|bearer token|client secret|private key|"
    r"password|passphrase|recovery code|seed phrase)\b"
)


def normalize_memory_content(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def memory_search_query(value: str, limit: int = 12) -> str | None:
    words = []
    for word in re.findall(r"[^\W_]{2,}", normalize_memory_content(value), flags=re.UNICODE):
        if word in SEARCH_STOP_WORDS or word in words:
            continue
        words.append(word)
        if len(words) >= limit:
            break
    return " OR ".join(f'"{word}"' for word in words) or None


def memory_candidate_is_sensitive(value: str) -> bool:
    text = " ".join(str(value or "").split())
    if not text:
        return False
    return redact_sensitive_text(text) != text or SENSITIVE_MEMORY_PATTERN.search(text) is not None


def memory_response(row, *, can_undo: bool = False) -> dict:
    return {
        "id": row.id,
        "scope": row.tier,
        "scope_id": row.tier_ref_id,
        "content": row.content,
        "status": row.status,
        "confidence": row.confidence,
        "source_type": row.source_type,
        "source_message_id": row.source_message_id,
        "source_turn_id": row.source_turn_id,
        "extractor_provider": row.extractor_provider,
        "extractor_model": row.extractor_model,
        "extractor_version": row.extractor_version,
        "supersedes_id": row.supersedes_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "reviewed_at": row.reviewed_at,
        "forgotten_at": row.forgotten_at,
        "can_undo": can_undo,
    }


def memory_event_response(row) -> dict:
    return {
        "id": row.id,
        "memory_id": row.memory_id,
        "related_memory_id": row.related_memory_id,
        "action": row.action,
        "from_status": row.from_status,
        "to_status": row.to_status,
        "created_at": row.created_at,
        "undone_at": row.undone_at,
    }


class MemoryService:
    def __init__(
        self,
        session_factory,
        secret_store,
        task_models,
        jobs: JobService,
        logger,
        candidate_limit: int = 5,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.task_models = task_models
        self.jobs = jobs
        self.logger = logger
        self.candidate_limit = min(10, max(1, int(candidate_limit)))

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def list(self, user_id: str, scope=None, scope_id=None, status=None) -> list[dict]:
        statuses = None
        if status:
            statuses = {item.strip() for item in str(status).split(",") if item.strip()}
            if not statuses or not statuses.issubset(MEMORY_STATUSES):
                raise RequestError("invalid memory status", 400)
        with self._uow() as uow:
            rows = uow.repo.memories(user_id, scope, scope_id, statuses)
            return [
                memory_response(
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                )
                for row in rows
            ]

    def create(self, user_id: str, values: dict) -> dict:
        try:
            with self._uow() as uow:
                scope = str(values.get("scope") or "global")
                scope_id = uow.repo.validate_memory_scope(user_id, scope, values.get("scope_id"))
                content = self._content(values.get("content"))
                normalized = normalize_memory_content(content)
                if uow.repo.live_memory_duplicate(user_id, scope, scope_id, normalized):
                    raise ConflictError("An active or pending memory already contains this text in that scope.")
                row = uow.repo.create_memory(
                    user_id=user_id,
                    scope=scope,
                    scope_id=scope_id,
                    content=content,
                    normalized_content=normalized,
                    status="active",
                    source_type="manual",
                )
                uow.repo.add_memory_event(
                    row,
                    "created",
                    from_status=None,
                    to_status="active",
                )
                return memory_response(row)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("An active or pending memory already contains this text in that scope.") from exc

    def revise(self, user_id: str, memory_id: str, values: dict) -> dict:
        try:
            with self._uow() as uow:
                old = uow.repo.memory(user_id, memory_id)
                if not old:
                    raise NotFoundError("memory not found")
                if old.status == "superseded":
                    raise ConflictError("A superseded memory cannot be edited.")
                scope = str(values.get("scope") or old.tier)
                scope_id_value = values.get("scope_id") if "scope_id" in values else old.tier_ref_id
                scope_id = uow.repo.validate_memory_scope(user_id, scope, scope_id_value)
                content = self._content(values.get("content", old.content))
                normalized = normalize_memory_content(content)
                duplicate = uow.repo.live_memory_duplicate(
                    user_id,
                    scope,
                    scope_id,
                    normalized,
                    excluding_id=old.id,
                )
                if duplicate:
                    raise ConflictError("An active or pending memory already contains this text in that scope.")
                previous_status = old.status
                snapshot = {
                    "reviewed_at": old.reviewed_at,
                    "forgotten_at": old.forgotten_at,
                }
                stamp = now_ts()
                old.status = "superseded"
                old.updated_at = stamp
                old.reviewed_at = stamp
                old.forgotten_at = None
                uow.session.flush()
                new_status = "active" if previous_status == "active" else "pending"
                row = uow.repo.create_memory(
                    user_id=user_id,
                    scope=scope,
                    scope_id=scope_id,
                    content=content,
                    normalized_content=normalized,
                    status=new_status,
                    source_type="edit",
                    source_message_id=old.source_message_id,
                    source_turn_id=old.source_turn_id,
                    confidence=old.confidence,
                    supersedes_id=old.id,
                    extractor_provider=old.extractor_provider,
                    extractor_model=old.extractor_model,
                    extractor_version=old.extractor_version,
                )
                uow.repo.add_memory_event(
                    old,
                    "superseded",
                    from_status=previous_status,
                    to_status="superseded",
                    related_memory_id=row.id,
                )
                uow.repo.add_memory_event(
                    row,
                    "edited",
                    from_status=previous_status,
                    to_status=new_status,
                    related_memory_id=old.id,
                    snapshot=snapshot,
                )
                return memory_response(row, can_undo=True)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("An active or pending memory already contains this text in that scope.") from exc

    def approve(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "approved", {"pending"}, "active")

    def reject(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "rejected", {"pending"}, "rejected")

    def forget(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "forgotten", {"pending", "active"}, "forgotten")

    def _transition(self, user_id, memory_id, action, allowed, target) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.memory(user_id, memory_id)
                if not row:
                    raise NotFoundError("memory not found")
                if row.status == target:
                    return memory_response(
                        row,
                        can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                    )
                if row.status not in allowed:
                    raise ConflictError(f"Memory status {row.status} cannot transition to {target}.")
                previous = row.status
                snapshot = {"reviewed_at": row.reviewed_at, "forgotten_at": row.forgotten_at}
                stamp = now_ts()
                row.status = target
                row.updated_at = stamp
                row.reviewed_at = stamp
                row.forgotten_at = stamp if target == "forgotten" else None
                uow.repo.add_memory_event(
                    row,
                    action,
                    from_status=previous,
                    to_status=target,
                    snapshot=snapshot,
                )
                return memory_response(row, can_undo=True)
        except IntegrityError as exc:
            raise ConflictError("This memory conflicts with another active memory in the same scope.") from exc

    def undo(self, user_id: str, memory_id: str) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.memory(user_id, memory_id)
                if not row:
                    raise NotFoundError("memory not found")
                event = uow.repo.latest_undoable_memory_event(user_id, memory_id)
                if not event:
                    raise ConflictError("There is no memory action to undo.")
                try:
                    snapshot = json.loads(event.snapshot_json or "{}")
                except (TypeError, ValueError):
                    snapshot = {}
                stamp = now_ts()
                if event.action == "edited":
                    previous = uow.repo.memory(user_id, event.related_memory_id) if event.related_memory_id else None
                    if not previous:
                        raise ConflictError("The superseded memory revision is unavailable.")
                    row.status = "superseded"
                    row.updated_at = stamp
                    row.reviewed_at = stamp
                    row.forgotten_at = None
                    uow.session.flush()
                    previous.status = event.from_status or "active"
                    previous.updated_at = stamp
                    previous.reviewed_at = snapshot.get("reviewed_at")
                    previous.forgotten_at = snapshot.get("forgotten_at")
                    event.undone_at = stamp
                    uow.repo.add_memory_event(
                        previous,
                        "undo_edit",
                        from_status="superseded",
                        to_status=previous.status,
                        related_memory_id=row.id,
                    )
                    return memory_response(previous)
                row.status = event.from_status or "pending"
                row.updated_at = stamp
                row.reviewed_at = snapshot.get("reviewed_at")
                row.forgotten_at = snapshot.get("forgotten_at")
                event.undone_at = stamp
                uow.repo.add_memory_event(
                    row,
                    f"undo_{event.action}",
                    from_status=event.to_status,
                    to_status=row.status,
                )
                return memory_response(
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                )
        except IntegrityError as exc:
            raise ConflictError("Undo would conflict with another active memory in the same scope.") from exc

    def history(self, user_id: str, memory_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.memory(user_id, memory_id)
            if not row:
                raise NotFoundError("memory not found")
            return {
                "memory": memory_response(
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                ),
                "events": [memory_event_response(event) for event in uow.repo.memory_events(user_id, memory_id)],
            }

    def prepare_extraction_job(self, repo, *, user_id: str, chat_id: str) -> str:
        return repo.add_job(
            user_id=user_id,
            chat_id=chat_id,
            turn_id=None,
            kind="memory_extraction",
            progress="Queued for memory review",
        ).id

    def submit_extraction(
        self,
        *,
        job_id: str,
        user_id: str,
        chat_id: str,
        turn_id: str,
        message_id: str,
        user_text: str,
        workspace_id: str | None,
        persona_id: str | None,
    ) -> None:
        def execute(token):
            try:
                outcome = self.task_models.run(
                    user_id,
                    MEMORY_EXTRACTION,
                    MemoryExtractionTaskInput(user_text=user_text, max_candidates=self.candidate_limit),
                    token,
                    chat_id=chat_id,
                    turn_id=turn_id,
                )
            except ProviderError as exc:
                if exc.code == "invalid_task_output":
                    raise ProviderError(
                        provider="memory_extractor",
                        code="invalid_memory_extraction",
                        user_message="Memory candidate extraction returned an invalid response.",
                        retryable=True,
                    ) from exc
                raise
            safe_candidates = [
                candidate
                for candidate in outcome.output.candidates
                if not memory_candidate_is_sensitive(candidate.content)
            ]
            return {
                "candidates": [
                    {
                        "content": candidate.content,
                        "scope": candidate.scope,
                        "confidence": candidate.confidence,
                    }
                    for candidate in safe_candidates
                ],
                "filtered_sensitive_count": len(outcome.output.candidates) - len(safe_candidates),
                "task_run_id": outcome.run_id,
                "task_provider": outcome.provider,
                "task_model": outcome.model,
            }

        def on_success(repo, result):
            created = []
            for candidate in (result or {}).get("candidates") or []:
                if memory_candidate_is_sensitive(candidate.get("content")):
                    continue
                scope = candidate["scope"]
                scope_id = {
                    "global": None,
                    "workspace": workspace_id,
                    "persona": persona_id,
                    "chat": chat_id,
                }.get(scope)
                if scope != "global" and not scope_id:
                    scope, scope_id = "chat", chat_id
                try:
                    scope_id = repo.validate_memory_scope(user_id, scope, scope_id)
                except (LookupError, ValueError):
                    scope, scope_id = "chat", chat_id
                normalized = normalize_memory_content(candidate["content"])
                if repo.live_memory_duplicate(user_id, scope, scope_id, normalized):
                    continue
                row = repo.create_memory(
                    user_id=user_id,
                    scope=scope,
                    scope_id=scope_id,
                    content=candidate["content"],
                    normalized_content=normalized,
                    status="pending",
                    source_type="conversation",
                    source_message_id=message_id,
                    source_turn_id=turn_id,
                    confidence=candidate["confidence"],
                    extractor_provider=(result or {}).get("task_provider"),
                    extractor_model=(result or {}).get("task_model"),
                    extractor_version=EXTRACTOR_VERSION,
                )
                repo.add_memory_event(
                    row,
                    "candidate_created",
                    from_status=None,
                    to_status="pending",
                )
                created.append(row.id)
            return {
                "candidate_count": len(created),
                "candidate_ids": created,
                "filtered_sensitive_count": (result or {}).get("filtered_sensitive_count", 0),
                "task_run_id": (result or {}).get("task_run_id"),
            }

        try:
            self.jobs.submit(
                job_id=job_id,
                job_type="memory_extraction",
                user_id=user_id,
                chat_id=chat_id,
                turn_id=None,
                latency_class="standard",
                model_key=f"task:{MEMORY_EXTRACTION}",
                execution=JobExecution(execute=execute, on_success=on_success),
            )
        except Exception:
            self.jobs.fail_unsubmitted(job_id, "Memory candidate extraction could not start.")
            raise

    @staticmethod
    def _content(value) -> str:
        content = " ".join(str(value or "").split())
        if not content:
            raise ValueError("memory content is required")
        if len(content) > 8000:
            raise ValueError("memory content is too long")
        return content
