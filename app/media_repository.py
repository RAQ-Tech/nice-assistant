"""Reading and writing pictures, presets, the media catalog and visual identity.

Half of `ApplicationRepository` was the image side of the product. It lives
here as a base class, so a unit of work still has one repository and every
caller still writes `uow.repo.media(...)`.
"""

from __future__ import annotations

import json
import secrets

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from app.clock import now_ts
from app.models import (
    AudioFile,
    CapabilityRequest,
    ChatAttachmentFrame,
    Chat,
    MediaCatalogResource,
    MediaCatalogSetting,
    MediaExecutionPlan,
    MediaPresetSignals,
    MediaFile,
    MediaGenerationAttempt,
    MediaGenerationJournal,
    MediaGenerationJournalStage,
    MediaGenerationPreset,
    MediaResourceCompatibility,
    Persona,
    PersonaImageLibraryEntry,
    PersonaPhotoSet,
    PersonaSceneBacklogEntry,
    PersonaIdentityEvent,
    PersonaIdentityReference,
    PersonaIdentityValidation,
    PersonaVisualIdentity,
    Workspace,
    IdentityValidationSetting,
)
from app.service_errors import ConflictError
from app.secret_store import SecretStore


class MediaRepository:
    session: OrmSession
    secret_store: SecretStore

    # Media catalog and deterministic execution plans
    def media_catalog_setting(self, user_id: str):
        row = self.session.get(MediaCatalogSetting, user_id)
        if row:
            return row
        # A new account's first page load asks for the catalog and for picture
        # readiness at once, and both found no row and both inserted one; the
        # second failed the unique key and answered 500. Inserting only when
        # absent, then reading whichever row won, makes the pair safe.
        stamp = now_ts()
        self.session.execute(
            sqlite_insert(MediaCatalogSetting)
            .values(
                user_id=user_id,
                vram_budget_mb=10240,
                max_loras=4,
                legacy_imported=0,
                created_at=stamp,
                updated_at=stamp,
            )
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        return self.session.get(MediaCatalogSetting, user_id, populate_existing=True)

    def save_media_catalog_setting(self, user_id: str, values: dict):
        row = self.media_catalog_setting(user_id)
        for field in ("vram_budget_mb", "max_loras", "legacy_imported"):
            if field in values:
                setattr(row, field, values[field])
        row.updated_at = now_ts()
        self.session.flush()
        return row

    def media_catalog_resources(self, user_id: str, *, enabled: bool | None = None):
        query = select(MediaCatalogResource).where(MediaCatalogResource.user_id == user_id)
        if enabled is not None:
            query = query.where(MediaCatalogResource.enabled == int(enabled))
        return self.session.scalars(
            query.order_by(
                MediaCatalogResource.resource_type,
                MediaCatalogResource.priority.desc(),
                MediaCatalogResource.name,
                MediaCatalogResource.id,
            )
        ).all()

    def media_catalog_resource(self, user_id: str, resource_id: str):
        return self.session.scalar(
            select(MediaCatalogResource).where(
                MediaCatalogResource.id == resource_id,
                MediaCatalogResource.user_id == user_id,
            )
        )

    def add_media_catalog_resource(self, user_id: str, values: dict):
        stamp = now_ts()
        row = MediaCatalogResource(
            id=secrets.token_hex(12),
            user_id=user_id,
            revision=1,
            created_at=stamp,
            updated_at=stamp,
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_media_catalog_resource(self, row, values: dict):
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_at = now_ts()
        row.revision += 1
        self.session.flush()
        return row

    def delete_media_catalog_resource(self, user_id: str, resource_id: str) -> bool:
        row = self.media_catalog_resource(user_id, resource_id)
        if not row:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def media_resource_compatible_model_ids(self, resource_id: str) -> list[str]:
        return list(
            self.session.scalars(
                select(MediaResourceCompatibility.model_resource_id)
                .where(MediaResourceCompatibility.resource_id == resource_id)
                .order_by(MediaResourceCompatibility.model_resource_id)
            ).all()
        )

    def media_resources_compatible_with_model(self, user_id: str, model_id: str):
        resource_ids = list(
            self.session.scalars(
                select(MediaResourceCompatibility.resource_id).where(
                    MediaResourceCompatibility.model_resource_id == model_id
                )
            ).all()
        )
        if not resource_ids:
            return []
        return self.session.scalars(
            select(MediaCatalogResource).where(
                MediaCatalogResource.user_id == user_id,
                MediaCatalogResource.id.in_(resource_ids),
            )
        ).all()

    def media_compatibility_map(self, user_id: str) -> dict[str, set[str]]:
        resources = self.media_catalog_resources(user_id)
        owned = {row.id for row in resources}
        result: dict[str, set[str]] = {row.id: set() for row in resources}
        if not owned:
            return result
        rows = self.session.scalars(
            select(MediaResourceCompatibility).where(MediaResourceCompatibility.resource_id.in_(owned))
        ).all()
        for row in rows:
            if row.model_resource_id in owned:
                result.setdefault(row.resource_id, set()).add(row.model_resource_id)
        return result

    def replace_media_resource_compatibility(self, resource_id: str, model_ids: list[str]):
        self.session.execute(
            delete(MediaResourceCompatibility).where(MediaResourceCompatibility.resource_id == resource_id)
        )
        for model_id in sorted(set(model_ids)):
            self.session.add(
                MediaResourceCompatibility(
                    id=secrets.token_hex(12),
                    resource_id=resource_id,
                    model_resource_id=model_id,
                )
            )
        self.session.flush()

    def add_media_execution_plan(self, *, user_id: str, capability_request_id: str, values: dict):
        row = MediaExecutionPlan(
            id=secrets.token_hex(12),
            user_id=user_id,
            capability_request_id=capability_request_id,
            created_at=now_ts(),
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_media_execution_plan(self, row, values: dict):
        for key, value in values.items():
            setattr(row, key, value)
        self.session.flush()
        return row

    def media_execution_plan_for_capability(self, user_id: str, capability_request_id: str):
        return self.session.scalar(
            select(MediaExecutionPlan).where(
                MediaExecutionPlan.user_id == user_id,
                MediaExecutionPlan.capability_request_id == capability_request_id,
            )
        )

    def media_execution_plan(self, user_id: str, plan_id: str):
        return self.session.scalar(
            select(MediaExecutionPlan).where(
                MediaExecutionPlan.user_id == user_id,
                MediaExecutionPlan.id == plan_id,
            )
        )

    # Consent-bound persona visual identity
    def identity_settings(self, user_id: str):
        return self.session.get(IdentityValidationSetting, user_id)

    def save_identity_settings(self, user_id: str, values: dict, *, preserve_secret: bool):
        stamp = now_ts()
        row = self.identity_settings(user_id)
        if not row:
            row = IdentityValidationSetting(user_id=user_id, created_at=stamp, updated_at=stamp)
            self.session.add(row)
        row.provider = values.get("provider", row.provider or "disabled")
        row.base_url = values.get("base_url", row.base_url)
        row.timeout_seconds = float(values.get("timeout_seconds", row.timeout_seconds or 15))
        if not preserve_secret:
            row.api_key_encrypted = self.secret_store.encrypt(values.get("api_key"))
        row.updated_at = stamp
        self.session.flush()
        return row

    def visual_identity(self, user_id: str, persona_id: str):
        return self.session.scalar(
            select(PersonaVisualIdentity).where(
                PersonaVisualIdentity.user_id == user_id,
                PersonaVisualIdentity.persona_id == persona_id,
            )
        )

    def visual_identity_by_id(self, identity_id: str):
        return self.session.get(PersonaVisualIdentity, identity_id)

    def create_visual_identity(self, user_id: str, persona_id: str):
        stamp = now_ts()
        row = PersonaVisualIdentity(
            id=secrets.token_hex(12),
            user_id=user_id,
            persona_id=persona_id,
            status="draft",
            consent_status="not_granted",
            acceptance_threshold=0.78,
            max_generation_attempts=2,
            failure_policy="show_unverified",
            revision=1,
            last_validation_sequence=0,
            last_event_sequence=0,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def identity_references(self, user_id: str, identity_id: str, *, include_deleted: bool = False):
        query = select(PersonaIdentityReference).where(
            PersonaIdentityReference.user_id == user_id,
            PersonaIdentityReference.identity_id == identity_id,
        )
        if not include_deleted:
            query = query.where(PersonaIdentityReference.review_status != "deleted")
        return self.session.scalars(
            query.order_by(PersonaIdentityReference.created_at, PersonaIdentityReference.id)
        ).all()

    def approved_identity_references(self, user_id: str, identity_id: str):
        return self.session.scalars(
            select(PersonaIdentityReference)
            .where(
                PersonaIdentityReference.user_id == user_id,
                PersonaIdentityReference.identity_id == identity_id,
                PersonaIdentityReference.review_status == "approved",
            )
            .order_by(PersonaIdentityReference.is_primary.desc(), PersonaIdentityReference.created_at)
        ).all()

    def identity_reference(self, user_id: str, reference_id: str):
        return self.session.scalar(
            select(PersonaIdentityReference).where(
                PersonaIdentityReference.id == reference_id,
                PersonaIdentityReference.user_id == user_id,
            )
        )

    def add_identity_reference(self, **values):
        row = PersonaIdentityReference(id=secrets.token_hex(12), **values)
        self.session.add(row)
        self.session.flush()
        return row

    def identity_validations(self, user_id: str, persona_id: str, limit: int = 50):
        return self.session.scalars(
            select(PersonaIdentityValidation)
            .where(
                PersonaIdentityValidation.user_id == user_id,
                PersonaIdentityValidation.persona_id == persona_id,
            )
            .order_by(PersonaIdentityValidation.sequence_number.desc())
            .limit(limit)
        ).all()

    def identity_validation(self, user_id: str, validation_id: str):
        return self.session.scalar(
            select(PersonaIdentityValidation).where(
                PersonaIdentityValidation.id == validation_id,
                PersonaIdentityValidation.user_id == user_id,
            )
        )

    def identity_validation_by_id(self, validation_id: str):
        return self.session.get(PersonaIdentityValidation, validation_id)

    def latest_media_identity_validation(self, user_id: str, media_id: str):
        return self.session.scalar(
            select(PersonaIdentityValidation)
            .where(
                PersonaIdentityValidation.user_id == user_id,
                PersonaIdentityValidation.candidate_media_id == media_id,
            )
            .order_by(PersonaIdentityValidation.created_order.desc())
            .limit(1)
        )

    def add_identity_validation(self, **values):
        created_order = self.session.scalar(
            update(IdentityValidationSetting)
            .where(IdentityValidationSetting.user_id == values["user_id"])
            .values(last_validation_order=IdentityValidationSetting.last_validation_order + 1)
            .returning(IdentityValidationSetting.last_validation_order)
        )
        if created_order is None:
            raise LookupError("identity validation settings not found")
        sequence = self.session.scalar(
            update(PersonaVisualIdentity)
            .where(PersonaVisualIdentity.id == values["identity_id"])
            .values(last_validation_sequence=PersonaVisualIdentity.last_validation_sequence + 1)
            .returning(PersonaVisualIdentity.last_validation_sequence)
        )
        if sequence is None:
            raise LookupError("visual identity not found")
        row = PersonaIdentityValidation(
            id=secrets.token_hex(12),
            sequence_number=sequence,
            created_order=created_order,
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_identity_event(
        self,
        identity,
        action: str,
        *,
        reference_id: str | None = None,
        validation_id: str | None = None,
        detail: dict | None = None,
    ):
        sequence = self.session.scalar(
            update(PersonaVisualIdentity)
            .where(PersonaVisualIdentity.id == identity.id)
            .values(last_event_sequence=PersonaVisualIdentity.last_event_sequence + 1)
            .returning(PersonaVisualIdentity.last_event_sequence)
        )
        if sequence is None:
            raise LookupError("visual identity not found")
        row = PersonaIdentityEvent(
            id=secrets.token_hex(12),
            user_id=identity.user_id,
            identity_id=identity.id,
            persona_id=identity.persona_id,
            reference_id=reference_id,
            validation_id=validation_id,
            sequence_number=sequence,
            action=action,
            detail_json=json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False),
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def identity_events(self, user_id: str, identity_id: str, limit: int = 100):
        return self.session.scalars(
            select(PersonaIdentityEvent)
            .where(PersonaIdentityEvent.user_id == user_id, PersonaIdentityEvent.identity_id == identity_id)
            .order_by(PersonaIdentityEvent.sequence_number.desc())
            .limit(limit)
        ).all()

    # Protected artifacts
    def media(self, user_id: str, media_id: str):
        return self.session.scalar(select(MediaFile).where(MediaFile.id == media_id, MediaFile.user_id == user_id))

    def media_items(self, user_id: str, *, kind: str | None = None, limit: int = 100):
        query = select(MediaFile).where(MediaFile.user_id == user_id)
        if kind is not None:
            query = query.where(MediaFile.kind == kind)
        return self.session.scalars(query.order_by(MediaFile.created_at.desc(), MediaFile.id.desc()).limit(limit)).all()

    def media_by_filename(self, user_id: str, kind: str, filename: str):
        return self.session.scalar(
            select(MediaFile)
            .where(
                MediaFile.user_id == user_id,
                MediaFile.kind == kind,
                MediaFile.filename == filename,
            )
            .order_by(MediaFile.created_at.desc())
        )

    def audio(self, user_id: str, audio_id: str):
        return self.session.scalar(select(AudioFile).where(AudioFile.id == audio_id, AudioFile.user_id == user_id))

    def audio_by_path(self, local_path: str):
        return self.session.scalar(select(AudioFile).where(AudioFile.local_path == local_path))

    def add_audio(
        self,
        *,
        audio_id: str,
        user_id: str,
        persona_id: str | None,
        chat_id: str | None,
        fmt: str,
        local_path: str,
    ) -> AudioFile:
        row = AudioFile(
            id=audio_id,
            user_id=user_id,
            persona_id=persona_id,
            chat_id=chat_id,
            format=fmt,
            local_path=local_path,
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_media(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        kind: str,
        filename: str,
        local_path: str,
        generation_plan_id: str | None = None,
    ) -> MediaFile:
        row = MediaFile(
            id=secrets.token_hex(8),
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
            filename=filename,
            local_path=local_path,
            generation_plan_id=generation_plan_id,
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_media_generation_attempt(
        self,
        *,
        user_id: str,
        media_plan_id: str,
        attempt_number: int,
        operation: str,
        source_media_id: str | None,
        workflow_resource_id: str | None,
    ):
        row = MediaGenerationAttempt(
            id=secrets.token_hex(12),
            user_id=user_id,
            media_plan_id=media_plan_id,
            attempt_number=attempt_number,
            operation=operation,
            status="running",
            source_media_id=source_media_id,
            workflow_resource_id=workflow_resource_id,
            started_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def media_generation_attempt_by_id(self, attempt_id: str):
        return self.session.get(MediaGenerationAttempt, attempt_id)

    def media_generation_attempts(self, user_id: str, media_plan_id: str):
        return self.session.scalars(
            select(MediaGenerationAttempt)
            .where(
                MediaGenerationAttempt.user_id == user_id,
                MediaGenerationAttempt.media_plan_id == media_plan_id,
            )
            .order_by(MediaGenerationAttempt.attempt_number)
        ).all()

    def media_presets(self, user_id: str, *, kind: str | None = None, enabled: bool | None = None):
        query = select(MediaGenerationPreset).where(MediaGenerationPreset.user_id == user_id)
        if kind:
            query = query.where(MediaGenerationPreset.kind == kind)
        if enabled is not None:
            query = query.where(MediaGenerationPreset.enabled == (1 if enabled else 0))
        return self.session.scalars(
            query.order_by(
                MediaGenerationPreset.priority.desc(),
                MediaGenerationPreset.name,
                MediaGenerationPreset.id,
            )
        ).all()

    def media_preset(self, user_id: str, preset_id: str):
        return self.session.scalar(
            select(MediaGenerationPreset).where(
                MediaGenerationPreset.user_id == user_id,
                MediaGenerationPreset.id == preset_id,
            )
        )

    def media_preset_by_name(self, user_id: str, name: str):
        return self.session.scalar(
            select(MediaGenerationPreset).where(
                MediaGenerationPreset.user_id == user_id,
                MediaGenerationPreset.name == name,
            )
        )

    def add_media_preset(self, *, user_id: str, values: dict):
        stamp = now_ts()
        row = MediaGenerationPreset(
            id=secrets.token_hex(12),
            user_id=user_id,
            created_at=stamp,
            updated_at=stamp,
            revision=1,
            **values,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("a preset with that name already exists") from exc
        return row

    def update_media_preset(self, row, values: dict):
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = now_ts()
        row.revision += 1
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("a preset with that name already exists") from exc
        return row

    def delete_media_preset(self, row) -> None:
        self.session.delete(row)

    def persona_for_user(self, user_id: str, persona_id: str):
        return self.session.scalar(
            select(Persona)
            .join(Workspace, Workspace.id == Persona.workspace_id)
            .where(Persona.id == persona_id, Workspace.user_id == user_id)
        )

    def recent_persona_themes(self, user_id: str, persona_id: str, *, limit: int = 10) -> list[str]:
        """What this persona's recent conversations were about.

        Chat titles are used because they are already a short, generated summary
        of a conversation. Re-summarising message bodies here would put private
        conversation content into a second place for no extra signal.
        """

        rows = self.session.scalars(
            select(Chat)
            .where(Chat.user_id == user_id, Chat.persona_id == persona_id, Chat.title.is_not(None))
            .order_by(Chat.updated_at.desc())
            .limit(max(1, min(int(limit), 50)))
        ).all()
        return [str(row.title).strip() for row in rows if str(row.title or "").strip()]

    def scene_backlog_entries(self, user_id: str, *, persona_id: str | None = None, state: str | None = None):
        query = select(PersonaSceneBacklogEntry).where(PersonaSceneBacklogEntry.user_id == user_id)
        if persona_id:
            query = query.where(PersonaSceneBacklogEntry.persona_id == persona_id)
        if state:
            query = query.where(PersonaSceneBacklogEntry.state == state)
        return self.session.scalars(
            query.order_by(PersonaSceneBacklogEntry.created_at.desc(), PersonaSceneBacklogEntry.id)
        ).all()

    def scene_backlog_entry(self, user_id: str, entry_id: str):
        return self.session.scalar(
            select(PersonaSceneBacklogEntry).where(
                PersonaSceneBacklogEntry.user_id == user_id,
                PersonaSceneBacklogEntry.id == entry_id,
            )
        )

    def scene_backlog_count(self, user_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(PersonaSceneBacklogEntry)
                .where(
                    PersonaSceneBacklogEntry.user_id == user_id,
                    PersonaSceneBacklogEntry.state != "retired",
                )
            )
            or 0
        )

    def approved_scene_backlog_entries(self, user_id: str, *, limit: int = 1):
        """The scenes waiting to be made, oldest first.

        Oldest first because a proposal that has been approved for a week has
        waited longer than one approved this evening, and nothing else
        distinguishes them.
        """

        return self.session.scalars(
            select(PersonaSceneBacklogEntry)
            .where(
                PersonaSceneBacklogEntry.user_id == user_id,
                PersonaSceneBacklogEntry.state == "approved",
            )
            .order_by(PersonaSceneBacklogEntry.created_at, PersonaSceneBacklogEntry.id)
            .limit(max(1, int(limit)))
        ).all()

    def preset_for_media(self, user_id: str, media_id: str) -> str:
        """Which preset made this picture, if a coordinator plan made it."""

        media = self.media_for_user(user_id, media_id)
        if not media or not media.generation_plan_id:
            return ""
        plan = self.session.get(MediaExecutionPlan, media.generation_plan_id)
        return str(getattr(plan, "preset_id", "") or "") if plan else ""

    def record_preset_signal(self, user_id: str, *, persona_id: str | None, preset_id: str, kind: str) -> None:
        """Add one to a count. Never subtracts; removal is its own count."""

        if not preset_id or kind not in {"kept", "sent_again", "removed"}:
            return
        row = self.session.scalar(
            select(MediaPresetSignals).where(
                MediaPresetSignals.user_id == user_id,
                MediaPresetSignals.persona_id.is_(None)
                if persona_id is None
                else MediaPresetSignals.persona_id == persona_id,
                MediaPresetSignals.preset_id == preset_id,
            )
        )
        if not row:
            row = MediaPresetSignals(
                id=secrets.token_hex(12),
                user_id=user_id,
                persona_id=persona_id,
                preset_id=preset_id,
                updated_at=now_ts(),
            )
            self.session.add(row)
        setattr(row, kind, int(getattr(row, kind, 0) or 0) + 1)
        row.updated_at = now_ts()
        self.session.flush()

    def preset_signals(self, user_id: str, *, persona_id: str | None = None):
        query = select(MediaPresetSignals).where(MediaPresetSignals.user_id == user_id)
        if persona_id is not None:
            query = query.where(MediaPresetSignals.persona_id == persona_id)
        return self.session.scalars(query.order_by(MediaPresetSignals.preset_id)).all()

    def clear_preset_signals(self, user_id: str, preset_id: str) -> int:
        rows = self.session.scalars(
            select(MediaPresetSignals).where(
                MediaPresetSignals.user_id == user_id,
                MediaPresetSignals.preset_id == preset_id,
            )
        ).all()
        for row in rows:
            self.session.delete(row)
        return len(rows)

    def add_attachment_frames(self, attachment_id: str, frames: list[tuple[str, int | None]]) -> None:
        """Record the extra frames shown beside an attachment, in order."""

        stamp = now_ts()
        for position, (media_id, frame_index) in enumerate(frames):
            self.session.add(
                ChatAttachmentFrame(
                    id=secrets.token_hex(12),
                    attachment_id=attachment_id,
                    media_id=media_id,
                    frame_index=frame_index,
                    position=position,
                    created_at=stamp,
                )
            )
        self.session.flush()

    def attachment_frames(self, attachment_id: str):
        return self.session.scalars(
            select(ChatAttachmentFrame)
            .where(ChatAttachmentFrame.attachment_id == attachment_id)
            .order_by(ChatAttachmentFrame.position)
        ).all()

    def library_entry_for_media(self, user_id: str, media_id: str):
        return self.session.scalar(
            select(PersonaImageLibraryEntry).where(
                PersonaImageLibraryEntry.user_id == user_id,
                PersonaImageLibraryEntry.media_id == media_id,
            )
        )

    def unsettled_photo_set_requests(self, user_id: str, set_id: str, *, exclude: str = "") -> int:
        """How many frames of this set have not finished one way or the other.

        Frames are identified by the idempotency key their request was created
        with, which is where the set and frame already live. Adding a column to
        capability requests for one feature's benefit would be the worse trade.
        """

        query = (
            select(func.count())
            .select_from(CapabilityRequest)
            .where(
                CapabilityRequest.user_id == user_id,
                CapabilityRequest.idempotency_key.like(f"scene:{set_id}:%"),
                CapabilityRequest.status.in_(("queued", "running", "pending_confirmation")),
            )
        )
        if exclude:
            query = query.where(CapabilityRequest.id != exclude)
        return int(self.session.scalar(query) or 0)

    def add_photo_set(
        self, *, user_id: str, persona_id: str, scene_json: str, variations_json: str, base_seed: int, frame_count: int
    ):
        stamp = now_ts()
        row = PersonaPhotoSet(
            id=secrets.token_hex(12),
            user_id=user_id,
            persona_id=persona_id,
            scene_json=scene_json,
            variations_json=variations_json,
            state="planned",
            base_seed=base_seed,
            frame_count=frame_count,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def photo_set(self, user_id: str, set_id: str):
        return self.session.scalar(
            select(PersonaPhotoSet).where(PersonaPhotoSet.user_id == user_id, PersonaPhotoSet.id == set_id)
        )

    def photo_sets(self, user_id: str, *, persona_id: str | None = None, state: str | None = None):
        query = select(PersonaPhotoSet).where(PersonaPhotoSet.user_id == user_id)
        if persona_id:
            query = query.where(PersonaPhotoSet.persona_id == persona_id)
        if state:
            query = query.where(PersonaPhotoSet.state == state)
        return self.session.scalars(query.order_by(PersonaPhotoSet.created_at.desc(), PersonaPhotoSet.id)).all()

    def photo_set_frames(self, user_id: str, set_id: str):
        """The retained frames of a set, in frame order."""

        return self.session.scalars(
            select(PersonaImageLibraryEntry)
            .where(
                PersonaImageLibraryEntry.user_id == user_id,
                PersonaImageLibraryEntry.photo_set_id == set_id,
            )
            .order_by(PersonaImageLibraryEntry.frame_index)
        ).all()

    def planned_photo_sets(self, user_id: str, *, limit: int = 1):
        return self.session.scalars(
            select(PersonaPhotoSet)
            .where(PersonaPhotoSet.user_id == user_id, PersonaPhotoSet.state == "planned")
            .order_by(PersonaPhotoSet.created_at, PersonaPhotoSet.id)
            .limit(max(1, int(limit)))
        ).all()

    def owners_with_approved_scenes(self) -> list[str]:
        """Owners who have something waiting to be made.

        Narrower than listing every account: this is the only question
        background production asks, and asking it directly means production
        never enumerates people who have nothing to do with it.
        """

        return [
            str(value)
            for value in self.session.scalars(
                select(PersonaSceneBacklogEntry.user_id).where(PersonaSceneBacklogEntry.state == "approved").distinct()
            ).all()
        ]

    def scene_backlog_entry_for_capability(self, capability_request_id: str):
        return self.session.scalar(
            select(PersonaSceneBacklogEntry).where(
                PersonaSceneBacklogEntry.capability_request_id == capability_request_id
            )
        )

    def generating_scene_backlog_entries(self, user_id: str | None = None):
        """Entries that claim to be in production, whether or not they are."""

        query = select(PersonaSceneBacklogEntry).where(PersonaSceneBacklogEntry.state == "generating")
        if user_id:
            query = query.where(PersonaSceneBacklogEntry.user_id == user_id)
        return self.session.scalars(query).all()

    def add_scene_backlog_entry(
        self, *, user_id: str, persona_id: str, scene_json: str, source: str, source_detail: str
    ):
        stamp = now_ts()
        row = PersonaSceneBacklogEntry(
            id=secrets.token_hex(12),
            user_id=user_id,
            persona_id=persona_id,
            scene_json=scene_json,
            state="proposed",
            source=source,
            source_detail=source_detail,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_library_entry(
        self, *, user_id: str, persona_id: str | None, media_id: str, scene_json: str, origin_chat_id=None
    ):
        existing = self.session.scalar(
            select(PersonaImageLibraryEntry).where(
                PersonaImageLibraryEntry.user_id == user_id,
                PersonaImageLibraryEntry.media_id == media_id,
            )
        )
        if existing:
            return None
        row = PersonaImageLibraryEntry(
            id=secrets.token_hex(12),
            user_id=user_id,
            persona_id=persona_id,
            media_id=media_id,
            scene_json=scene_json,
            state="ready",
            origin_chat_id=origin_chat_id,
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def library_entry(self, user_id: str, entry_id: str):
        return self.session.scalar(
            select(PersonaImageLibraryEntry).where(
                PersonaImageLibraryEntry.user_id == user_id,
                PersonaImageLibraryEntry.id == entry_id,
            )
        )

    def library_entries(
        self, user_id: str, *, persona_id: str | None = None, state: str | None = None, limit: int = 100
    ):
        query = select(PersonaImageLibraryEntry).where(PersonaImageLibraryEntry.user_id == user_id)
        if persona_id:
            query = query.where(PersonaImageLibraryEntry.persona_id == persona_id)
        if state:
            query = query.where(PersonaImageLibraryEntry.state == state)
        return self.session.scalars(
            query.order_by(PersonaImageLibraryEntry.created_at.desc(), PersonaImageLibraryEntry.id).limit(
                max(1, min(int(limit), 500))
            )
        ).all()

    def retire_oldest_library_entries(self, user_id: str, *, keep: int) -> int:
        """Retire the oldest entries beyond the cap.

        Retired rather than deleted: the picture is still the owner's, and
        quietly removing files to save space is not this layer's decision.
        """

        rows = self.session.scalars(
            select(PersonaImageLibraryEntry)
            .where(
                PersonaImageLibraryEntry.user_id == user_id,
                PersonaImageLibraryEntry.state != "retired",
            )
            .order_by(PersonaImageLibraryEntry.created_at.desc(), PersonaImageLibraryEntry.id)
        ).all()
        retired = 0
        for row in rows[keep:]:
            row.state = "retired"
            retired += 1
        return retired

    def media_for_user(self, user_id: str, media_id: str):
        return self.session.scalar(select(MediaFile).where(MediaFile.user_id == user_id, MediaFile.id == media_id))

    def add_media_generation_journal(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        persona_id: str | None,
        media_plan_id: str | None,
        capability_request_id: str | None,
        kind: str,
        origin: str,
    ):
        row = MediaGenerationJournal(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            persona_id=persona_id,
            media_plan_id=media_plan_id,
            capability_request_id=capability_request_id,
            kind=kind,
            origin=origin,
            status="running",
            started_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def media_generation_journal_by_id(self, journal_id: str):
        return self.session.get(MediaGenerationJournal, journal_id)

    def media_generation_journal_for_media(self, user_id: str, media_id: str):
        return self.session.scalars(
            select(MediaGenerationJournal)
            .where(
                MediaGenerationJournal.user_id == user_id,
                MediaGenerationJournal.media_id == media_id,
            )
            .order_by(MediaGenerationJournal.started_at.desc())
            .limit(1)
        ).first()

    def media_generation_journals(self, user_id: str, *, limit: int = 50, offset: int = 0):
        return self.session.scalars(
            select(MediaGenerationJournal)
            .where(MediaGenerationJournal.user_id == user_id)
            .order_by(MediaGenerationJournal.started_at.desc(), MediaGenerationJournal.id)
            .limit(max(1, min(int(limit), 200)))
            .offset(max(0, int(offset)))
        ).all()

    def add_media_generation_journal_stage(
        self,
        *,
        journal_id: str,
        stage: str,
        status: str,
        summary: str,
        detail_json: str,
        started_at: int,
        duration_ms: int | None,
    ):
        sequence = (
            self.session.scalar(
                select(func.count())
                .select_from(MediaGenerationJournalStage)
                .where(MediaGenerationJournalStage.journal_id == journal_id)
            )
            or 0
        ) + 1
        row = MediaGenerationJournalStage(
            id=secrets.token_hex(12),
            journal_id=journal_id,
            sequence=sequence,
            stage=stage,
            status=status,
            summary=summary,
            detail_json=detail_json,
            started_at=started_at,
            duration_ms=duration_ms,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def media_generation_journal_stages(self, journal_id: str):
        return self.session.scalars(
            select(MediaGenerationJournalStage)
            .where(MediaGenerationJournalStage.journal_id == journal_id)
            .order_by(MediaGenerationJournalStage.sequence)
        ).all()

    def media_generation_journal_stage_count(self, journal_id: str) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(MediaGenerationJournalStage)
                .where(MediaGenerationJournalStage.journal_id == journal_id)
            )
            or 0
        )

    def delete_media_generation_journals_before(self, user_id: str, cutoff: int) -> int:
        """Drop journals whose generation finished before `cutoff`. Stages cascade."""

        rows = self.session.scalars(
            select(MediaGenerationJournal).where(
                MediaGenerationJournal.user_id == user_id,
                MediaGenerationJournal.started_at < cutoff,
                MediaGenerationJournal.status != "running",
            )
        ).all()
        for row in rows:
            self.session.execute(
                delete(MediaGenerationJournalStage).where(MediaGenerationJournalStage.journal_id == row.id)
            )
            self.session.delete(row)
        return len(rows)
