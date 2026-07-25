from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy.exc import IntegrityError

from app.auth import redact_sensitive_text
from app.chat_binding import resolve_chat_binding
from app.job_service import JobExecution, JobService
from app.provider_contracts import ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.task_contracts import MEMORY_EXTRACTION, MemoryExtractionTaskInput


EXTRACTOR_VERSION = "memory-candidates-task-v3"
MEMORY_STATUSES = {"pending", "active", "rejected", "forgotten", "superseded"}
MEMORY_TYPES = {"durable", "temporal", "stateful"}
MEMORY_VALIDITY_STATUSES = {"current", "stale", "expired"}
MEMORY_STATEFUL_STATUSES = {"active", "completed", "cancelled", "superseded"}
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


def memory_origin_response(row) -> dict:
    if not row:
        return {}
    try:
        evidence = json.loads(row.evidence_json or "{}")
    except (TypeError, ValueError):
        evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}
    return {
        "source_kind": row.source_kind,
        "source_chat_id": row.source_chat_id,
        "source_persona_id": row.source_persona_id,
        "source_workspace_id": row.source_workspace_id,
        "source_message_id": row.source_message_id,
        "source_turn_id": row.source_turn_id,
        "evidence": evidence,
        "provenance_status": row.provenance_status,
        "revision_of_memory_id": row.revision_of_memory_id,
        "created_at": row.created_at,
    }


def memory_grant_response(row) -> dict:
    target_id = row.persona_id if row.grant_type == "persona" else row.workspace_id
    return {
        "id": row.id,
        "grant_type": row.grant_type,
        "target_id": target_id,
        "grant_source": row.grant_source,
        "granted_by_human_id": row.granted_by_human_id,
        "granted_at": row.granted_at,
    }


def memory_grant_event_response(row) -> dict:
    return {
        "id": row.id,
        "memory_id": row.memory_id,
        "grant_id": row.grant_id,
        "action": row.action,
        "grant_type": row.grant_type,
        "target_id": row.target_id,
        "created_at": row.created_at,
    }


def memory_response(
    row,
    *,
    record=None,
    origin=None,
    grants=(),
    can_undo: bool = False,
) -> dict:
    # `scope` and `scope_id` are retained only so legacy records remain
    # diagnosable. Access is always determined from MemoryRecord + active grants.
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
        "access_state": record.access_state if record else "legacy_quarantined",
        "memory_type": record.memory_type if record else "legacy_unknown",
        "validity_status": record.validity_status if record else "legacy_unknown",
        "valid_until": record.valid_until if record else None,
        "stateful_status": record.stateful_status if record else None,
        "last_confirmed_at": record.last_confirmed_at if record else None,
        "origin": memory_origin_response(origin),
        "grants": [memory_grant_response(grant) for grant in grants],
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

    @staticmethod
    def _response(repo, user_id: str, row, *, can_undo: bool = False) -> dict:
        return memory_response(
            row,
            record=repo.memory_record(row.id),
            origin=repo.memory_origin(row.id),
            grants=repo.active_memory_grants(row.id),
            can_undo=can_undo,
        )

    @staticmethod
    def _require_mutable_memory(repo, row) -> None:
        record = repo.memory_record(row.id)
        origin = repo.memory_origin(row.id)
        if not record or record.access_state != "grants" or not origin or origin.provenance_status != "resolved":
            raise ConflictError(
                "This migrated memory is read-only until its access and origin are explicitly assigned."
            )

    @staticmethod
    def _validated_extraction_source(
        repo,
        *,
        user_id: str,
        chat_id: str,
        turn_id: str,
        message_id: str,
        user_text: str,
        workspace_id: str | None,
        persona_id: str | None,
    ) -> dict | None:
        chat = repo.chat(user_id, chat_id)
        source = repo.message(message_id)
        turn = repo.turn(user_id, turn_id)
        binding = resolve_chat_binding(repo, user_id, chat) if chat else None
        human = repo.human_principal(user_id)
        if not (
            chat
            and source
            and source.chat_id == chat_id
            and source.role == "user"
            and normalize_memory_content(source.text) == normalize_memory_content(user_text)
            and turn
            and turn.chat_id == chat_id
            and turn.user_message_id == message_id
            and binding
            and binding.can_continue
            and binding.human_id
            and binding.persona_id
            and persona_id == binding.persona_id
            and workspace_id == binding.workspace_id
            and human
            and human.id == binding.human_id
        ):
            return None
        return {
            "chat": chat,
            "source": source,
            "turn": turn,
            "binding": binding,
            "human": human,
        }

    @staticmethod
    def _metadata(values: dict, *, base=None, last_confirmed_at: int | None = None) -> dict:
        memory_type = str(
            values.get("memory_type") if "memory_type" in values else getattr(base, "memory_type", "durable")
        )
        validity_status = str(
            values.get("validity_status")
            if "validity_status" in values
            else getattr(base, "validity_status", "current")
        )
        valid_until = values.get("valid_until") if "valid_until" in values else getattr(base, "valid_until", None)
        stateful_status = (
            values.get("stateful_status") if "stateful_status" in values else getattr(base, "stateful_status", None)
        )
        if memory_type not in MEMORY_TYPES:
            raise ValueError("invalid memory type")
        if validity_status not in MEMORY_VALIDITY_STATUSES:
            raise ValueError("invalid memory validity status")
        if valid_until is not None:
            try:
                valid_until = int(valid_until)
            except (TypeError, ValueError) as exc:
                raise ValueError("valid_until must be a timestamp") from exc
            if valid_until <= 0:
                raise ValueError("valid_until must be a positive timestamp")
        if stateful_status is not None:
            stateful_status = str(stateful_status)
        if memory_type == "temporal":
            if valid_until is None:
                raise ValueError("temporal memories require valid_until")
            if stateful_status is not None:
                raise ValueError("temporal memories cannot have a stateful status")
        elif memory_type == "stateful":
            if stateful_status not in MEMORY_STATEFUL_STATUSES:
                raise ValueError("stateful memories require a lifecycle status")
            if valid_until is not None:
                raise ValueError("stateful memories cannot have valid_until")
        elif valid_until is not None or stateful_status is not None:
            raise ValueError("durable memories cannot have temporal or lifecycle metadata")
        return {
            "memory_type": memory_type,
            "validity_status": validity_status,
            "valid_until": valid_until,
            "stateful_status": stateful_status,
            "last_confirmed_at": (
                last_confirmed_at if last_confirmed_at is not None else getattr(base, "last_confirmed_at", None)
            ),
        }

    @staticmethod
    def _diagnostic_scope(grants: list[dict]) -> tuple[str, str]:
        first = sorted(
            grants,
            key=lambda value: (value["grant_type"], value["target_id"]),
        )[0]
        return first["grant_type"], first["target_id"]

    @staticmethod
    def _reject_sensitive(content: str) -> None:
        if memory_candidate_is_sensitive(content):
            raise ValueError("Credentials and credential-shaped content cannot be stored as memory.")

    def list(
        self,
        user_id: str,
        scope=None,
        scope_id=None,
        status=None,
        grant_type=None,
        grant_target_id=None,
    ) -> list[dict]:
        statuses = None
        if status:
            statuses = {item.strip() for item in str(status).split(",") if item.strip()}
            if not statuses or not statuses.issubset(MEMORY_STATUSES):
                raise RequestError("invalid memory status", 400)
        with self._uow() as uow:
            rows = uow.repo.memories(
                user_id,
                scope,
                scope_id,
                statuses,
                grant_type=grant_type,
                grant_target_id=grant_target_id,
            )
            return [
                self._response(
                    uow.repo,
                    user_id,
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                )
                for row in rows
            ]

    def create(self, user_id: str, values: dict) -> dict:
        try:
            with self._uow() as uow:
                human = uow.repo.human_principal(user_id)
                if not human:
                    raise LookupError("human principal not found")
                grants = uow.repo.validate_memory_grants(user_id, values.get("grants") or [])
                content = self._content(values.get("content"))
                self._reject_sensitive(content)
                normalized = normalize_memory_content(content)
                for grant in grants:
                    if grant["grant_type"] == "persona" and uow.repo.v3_memory_duplicate(
                        user_id, normalized, grant["target_id"]
                    ):
                        raise ConflictError("An active or pending memory already contains this text for that persona.")
                metadata = self._metadata(values, last_confirmed_at=now_ts())
                scope, scope_id = self._diagnostic_scope(grants)
                row = uow.repo.create_memory(
                    user_id=user_id,
                    scope=scope,
                    scope_id=scope_id,
                    content=content,
                    normalized_content=normalized,
                    status="active",
                    source_type="manual",
                )
                uow.repo.create_memory_record(
                    row.id,
                    human_id=human.id,
                    access_state="grants",
                    **metadata,
                )
                uow.repo.create_memory_origin(
                    row.id,
                    human_id=human.id,
                    source_kind="manual",
                    evidence={"explicit_owner_action": True},
                    provenance_status="resolved",
                )
                for grant in grants:
                    uow.repo.add_memory_grant(
                        row.id,
                        human_id=human.id,
                        grant_type=grant["grant_type"],
                        target_id=grant["target_id"],
                        grant_source="owner",
                        granted_by_human_id=human.id,
                    )
                uow.repo.add_memory_event(
                    row,
                    "created",
                    from_status=None,
                    to_status="active",
                )
                return self._response(uow.repo, user_id, row)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("An active or pending memory conflicts with this grant assignment.") from exc

    def propose(self, user_id: str, values: dict) -> dict:
        """Create an editable user proposal that requires review before context use."""

        try:
            with self._uow() as uow:
                source_message_id = str(values.get("source_message_id") or "").strip() or None
                if not source_message_id:
                    raise ValueError("source_message_id is required")
                source = uow.repo.message(source_message_id)
                source_chat = uow.repo.chat(user_id, source.chat_id) if source else None
                if not source or not source_chat:
                    raise NotFoundError("source message not found")
                binding = resolve_chat_binding(uow.repo, user_id, source_chat)
                if not binding.can_continue or not binding.human_id or not binding.persona_id:
                    raise ConflictError("The source chat no longer has a valid persona and access binding.")
                human = uow.repo.human_principal(user_id)
                if not human or human.id != binding.human_id:
                    raise ConflictError("The source chat owner binding is no longer valid.")
                content = self._content(values.get("content"))
                self._reject_sensitive(content)
                normalized = normalize_memory_content(content)
                if uow.repo.v3_memory_duplicate(user_id, normalized, binding.persona_id):
                    raise ConflictError(
                        "An active or pending memory already contains this text for the source persona."
                    )
                source_turn_id = None
                for turn in uow.repo.turns_for_chat(user_id, source_chat.id):
                    if source_message_id in {turn.user_message_id, turn.assistant_message_id}:
                        source_turn_id = turn.id
                        break
                row = uow.repo.create_memory(
                    user_id=user_id,
                    scope="persona",
                    scope_id=binding.persona_id,
                    content=content,
                    normalized_content=normalized,
                    status="pending",
                    source_type="manual",
                    source_message_id=source_message_id,
                    source_turn_id=source_turn_id,
                )
                uow.repo.create_memory_record(
                    row.id,
                    human_id=human.id,
                    access_state="grants",
                    memory_type="durable",
                    validity_status="current",
                    last_confirmed_at=now_ts(),
                )
                uow.repo.create_memory_origin(
                    row.id,
                    human_id=human.id,
                    source_kind="manual",
                    source_chat_id=source_chat.id,
                    source_persona_id=binding.persona_id,
                    source_workspace_id=binding.workspace_id,
                    source_message_id=source_message_id,
                    source_turn_id=source_turn_id,
                    evidence={"explicit_owner_proposal": True},
                    provenance_status="resolved",
                )
                uow.repo.add_memory_grant(
                    row.id,
                    human_id=human.id,
                    grant_type="persona",
                    target_id=binding.persona_id,
                    grant_source="owner",
                    granted_by_human_id=human.id,
                )
                uow.repo.add_memory_event(
                    row,
                    "candidate_created",
                    from_status=None,
                    to_status="pending",
                )
                return self._response(uow.repo, user_id, row)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("An active or pending memory conflicts with this source persona.") from exc

    def revise(self, user_id: str, memory_id: str, values: dict) -> dict:
        try:
            with self._uow() as uow:
                unexpected = set(values) - {
                    "content",
                    "memory_type",
                    "validity_status",
                    "valid_until",
                    "stateful_status",
                }
                if unexpected:
                    raise ValueError("Memory edits cannot change access or origin.")
                old = uow.repo.memory(user_id, memory_id)
                if not old:
                    raise NotFoundError("memory not found")
                if old.status == "superseded":
                    raise ConflictError("A superseded memory cannot be edited.")
                old_record = uow.repo.memory_record(old.id)
                old_grants = list(uow.repo.active_memory_grants(old.id))
                self._require_mutable_memory(uow.repo, old)
                content = self._content(values.get("content", old.content))
                self._reject_sensitive(content)
                normalized = normalize_memory_content(content)
                for grant in old_grants:
                    if grant.grant_type != "persona":
                        continue
                    if uow.repo.v3_memory_duplicate(
                        user_id,
                        normalized,
                        grant.persona_id,
                        excluding_id=old.id,
                    ):
                        raise ConflictError("An active or pending memory already contains this text for that persona.")
                metadata = self._metadata(values, base=old_record, last_confirmed_at=now_ts())
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
                    scope=old.tier,
                    scope_id=old.tier_ref_id,
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
                uow.repo.copy_memory_v3(old.id, row.id)
                new_record = uow.repo.memory_record(row.id)
                for field, value in metadata.items():
                    setattr(new_record, field, value)
                new_record.updated_at = stamp
                uow.session.flush()
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
                return self._response(uow.repo, user_id, row, can_undo=True)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("An active or pending memory conflicts with this revision.") from exc

    def replace_grants(self, user_id: str, memory_id: str, grants: list[dict]) -> dict:
        """Atomically validate and replace a memory's complete active grant set."""

        try:
            with self._uow() as uow:
                row = uow.repo.memory(user_id, memory_id)
                if not row:
                    raise NotFoundError("memory not found")
                self._require_mutable_memory(uow.repo, row)
                uow.repo.replace_memory_grants(user_id, memory_id, grants)
                return self._response(
                    uow.repo,
                    user_id,
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                )
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("The requested memory grants conflict with existing access.") from exc

    def approve(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "approved", {"pending"}, "active")

    def reject(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "rejected", {"pending"}, "rejected")

    def forget(self, user_id: str, memory_id: str) -> dict:
        return self._transition(user_id, memory_id, "forgotten", {"pending", "active"}, "forgotten")

    def delete(self, user_id: str, memory_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.memory(user_id, memory_id)
            if not row:
                raise NotFoundError("memory not found")
            deleted_id = row.id
            uow.repo.delete_memory(row)
            return {"ok": True, "id": deleted_id, "deleted": True}

    def bulk_action(self, user_id: str, action: str, memory_ids: list[str]) -> dict:
        ids = list(dict.fromkeys(str(value).strip() for value in memory_ids if str(value).strip()))
        if not ids:
            raise RequestError("At least one item must be selected.", 400)
        with self._uow() as uow:
            rows = uow.repo.memories_by_ids(user_id, ids)
            if len(rows) != len(ids):
                raise NotFoundError("One or more memories were not found.")
            if action == "delete":
                for row in rows:
                    uow.repo.delete_memory(row)
                affected = len(rows)
            elif action == "forget":
                for row in rows:
                    self._require_mutable_memory(uow.repo, row)
                invalid = [row for row in rows if row.status not in {"pending", "active", "forgotten"}]
                if invalid:
                    raise ConflictError("Only pending or active memories can be forgotten.")
                affected = 0
                for row in rows:
                    if row.status == "forgotten":
                        continue
                    previous = row.status
                    snapshot = {"reviewed_at": row.reviewed_at, "forgotten_at": row.forgotten_at}
                    stamp = now_ts()
                    row.status = "forgotten"
                    row.updated_at = stamp
                    row.reviewed_at = stamp
                    row.forgotten_at = stamp
                    uow.repo.add_memory_event(
                        row,
                        "forgotten",
                        from_status=previous,
                        to_status="forgotten",
                        snapshot=snapshot,
                    )
                    affected += 1
            else:
                raise RequestError("invalid memory bulk action", 400)
            return {
                "action": action,
                "requested_count": len(ids),
                "affected_count": affected,
                "ids": ids,
            }

    def _transition(self, user_id, memory_id, action, allowed, target) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.memory(user_id, memory_id)
                if not row:
                    raise NotFoundError("memory not found")
                self._require_mutable_memory(uow.repo, row)
                if row.status == target:
                    return self._response(
                        uow.repo,
                        user_id,
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
                return self._response(uow.repo, user_id, row, can_undo=True)
        except IntegrityError as exc:
            raise ConflictError("This memory conflicts with another active memory in the same scope.") from exc

    def undo(self, user_id: str, memory_id: str) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.memory(user_id, memory_id)
                if not row:
                    raise NotFoundError("memory not found")
                self._require_mutable_memory(uow.repo, row)
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
                    current_record = uow.repo.memory_record(row.id)
                    previous_record = uow.repo.memory_record(previous.id)
                    if not current_record or not previous_record:
                        raise ConflictError("The memory revision's access metadata is unavailable.")
                    grants_managed = current_record.access_state == "grants" or previous_record.access_state == "grants"
                    if grants_managed:
                        if current_record.access_state != "grants" or previous_record.access_state != "grants":
                            raise ConflictError("The memory revision's access metadata is inconsistent.")
                        try:
                            uow.repo.sync_memory_grants_from_revision(
                                user_id,
                                source_memory_id=row.id,
                                target_memory_id=previous.id,
                            )
                        except LookupError as exc:
                            raise ConflictError(
                                "The memory revision's access grants could not be restored safely."
                            ) from exc
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
                    return self._response(uow.repo, user_id, previous)
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
                return self._response(
                    uow.repo,
                    user_id,
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
                "memory": self._response(
                    uow.repo,
                    user_id,
                    row,
                    can_undo=uow.repo.latest_undoable_memory_event(user_id, row.id) is not None,
                ),
                "events": [memory_event_response(event) for event in uow.repo.memory_events(user_id, memory_id)],
                "grant_events": [
                    memory_grant_event_response(event) for event in uow.repo.memory_grant_events(user_id, memory_id)
                ],
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
            with self._uow() as source_uow:
                if not self._validated_extraction_source(
                    source_uow.repo,
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=turn_id,
                    message_id=message_id,
                    user_text=user_text,
                    workspace_id=workspace_id,
                    persona_id=persona_id,
                ):
                    return {
                        "candidates": [],
                        "filtered_sensitive_count": 0,
                        "source_binding_valid": False,
                        "task_run_id": None,
                        "task_provider": None,
                        "task_model": None,
                    }
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
                        "confidence": candidate.confidence,
                    }
                    for candidate in safe_candidates
                ],
                "filtered_sensitive_count": len(outcome.output.candidates) - len(safe_candidates),
                "source_binding_valid": True,
                "task_run_id": outcome.run_id,
                "task_provider": outcome.provider,
                "task_model": outcome.model,
            }

        def on_success(repo, result):
            validated = self._validated_extraction_source(
                repo,
                user_id=user_id,
                chat_id=chat_id,
                turn_id=turn_id,
                message_id=message_id,
                user_text=user_text,
                workspace_id=workspace_id,
                persona_id=persona_id,
            )
            if not validated:
                return {
                    "candidate_count": 0,
                    "candidate_ids": [],
                    "filtered_sensitive_count": (result or {}).get("filtered_sensitive_count", 0),
                    "source_binding_valid": False,
                    "task_run_id": (result or {}).get("task_run_id"),
                }
            source = validated["source"]
            binding = validated["binding"]
            human = validated["human"]
            created = []
            for candidate in (result or {}).get("candidates") or []:
                if memory_candidate_is_sensitive(candidate.get("content")):
                    continue
                content = self._content(candidate.get("content"))
                normalized = normalize_memory_content(content)
                if repo.v3_memory_duplicate(user_id, normalized, binding.persona_id):
                    continue
                row = repo.create_memory(
                    user_id=user_id,
                    scope="persona",
                    scope_id=binding.persona_id,
                    content=content,
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
                repo.create_memory_record(
                    row.id,
                    human_id=human.id,
                    access_state="grants",
                    memory_type="durable",
                    validity_status="current",
                    last_confirmed_at=source.created_at,
                )
                repo.create_memory_origin(
                    row.id,
                    human_id=human.id,
                    source_kind="conversation",
                    source_chat_id=chat_id,
                    source_persona_id=binding.persona_id,
                    source_workspace_id=binding.workspace_id,
                    source_message_id=message_id,
                    source_turn_id=turn_id,
                    evidence={"source_message_role": "user"},
                    provenance_status="resolved",
                )
                repo.add_memory_grant(
                    row.id,
                    human_id=human.id,
                    grant_type="persona",
                    target_id=binding.persona_id,
                    grant_source="automatic_source_persona",
                    granted_by_human_id=human.id,
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
                "source_binding_valid": True,
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
                ordering_key=f"chat:{chat_id}",
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
