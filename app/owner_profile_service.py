from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.memory_service import memory_candidate_is_sensitive
from app.repositories import UnitOfWork
from app.service_errors import ConflictError, NotFoundError, RequestError


OWNER_PROFILE_FIELDS = (
    "name",
    "name_pronunciation",
    "pronouns",
    "time_zone",
    "locale",
    "preferred_language",
    "measurement_units",
    "communication_needs",
    "accessibility_needs",
)
OWNER_PROFILE_FIELD_LIMITS = {
    "name": 240,
    "name_pronunciation": 500,
    "pronouns": 240,
    "time_zone": 160,
    "locale": 160,
    "preferred_language": 160,
    "measurement_units": 160,
    "communication_needs": 2000,
    "accessibility_needs": 2000,
}


def owner_profile_response(human_id: str, row) -> dict:
    return {
        "human_id": human_id,
        **{field: getattr(row, field, None) if row is not None else None for field in OWNER_PROFILE_FIELDS},
        "revision": int(getattr(row, "revision", 0) or 0),
        "created_at": int(getattr(row, "created_at", 0) or 0),
        "updated_at": int(getattr(row, "updated_at", 0) or 0),
    }


class OwnerProfileService:
    """Explicitly managed universal owner basics, isolated from memory extraction."""

    def __init__(self, session_factory, secret_store):
        self.session_factory = session_factory
        self.secret_store = secret_store

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def get(self, user_id: str) -> dict:
        with self._uow() as uow:
            human = uow.repo.human_principal(user_id)
            if not human:
                raise NotFoundError("human principal not found")
            return owner_profile_response(human.id, uow.repo.owner_profile(human.id))

    def update(self, user_id: str, values: dict) -> dict:
        unexpected = set(values) - set(OWNER_PROFILE_FIELDS)
        if unexpected:
            raise RequestError("Owner profile contains unsupported fields.", 400)
        try:
            normalized = {field: self._value(field, value) for field, value in values.items()}
            with self._uow() as uow:
                human = uow.repo.human_principal(user_id)
                if not human:
                    raise LookupError("human principal not found")
                current = uow.repo.owner_profile(human.id)
                changed = [field for field, value in normalized.items() if getattr(current, field, None) != value]
                if not changed:
                    return owner_profile_response(human.id, current)
                row = uow.repo.save_owner_profile(
                    human.id,
                    {field: normalized[field] for field in changed},
                )
                # The audit event deliberately records field names only. Universal
                # owner-profile values never appear in the event payload.
                action = (
                    "cleared" if not any(getattr(row, field, None) for field in OWNER_PROFILE_FIELDS) else "updated"
                )
                uow.repo.add_owner_profile_event(human.id, changed, action=action)
                return owner_profile_response(human.id, row)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc
        except IntegrityError as exc:
            raise ConflictError("The owner profile could not be updated.") from exc

    @staticmethod
    def _value(field: str, value) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        if not text:
            return None
        if len(text) > OWNER_PROFILE_FIELD_LIMITS[field]:
            raise ValueError(f"{field} is too long")
        if memory_candidate_is_sensitive(text):
            raise ValueError("Credentials and credential-shaped content cannot be stored in the owner profile.")
        return text


__all__ = [
    "OWNER_PROFILE_FIELDS",
    "OwnerProfileService",
    "owner_profile_response",
]
