from __future__ import annotations

from contextlib import AbstractContextManager
import json
import secrets
import time

from sqlalchemy import and_, delete, func, or_, select, text as sql_text, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    AppSetting,
    AsyncJob,
    AudioFile,
    CapabilityEvent,
    CapabilityRequest,
    Chat,
    ChatAttachment,
    ChatBinding,
    ConversationTurn,
    ConversationSummary,
    HumanPrincipal,
    MediaCatalogResource,
    MediaCatalogSetting,
    MediaExecutionPlan,
    MediaFile,
    MediaGenerationAttempt,
    MediaResourceCompatibility,
    Memory,
    MemoryEvent,
    MemoryGrant,
    MemoryGrantEvent,
    MemoryOrigin,
    MemoryRecord,
    Message,
    Persona,
    PersonaIdentityEvent,
    PersonaIdentityReference,
    PersonaIdentityValidation,
    PersonaVisualIdentity,
    PersonaWorkspaceLink,
    OwnerProfile,
    OwnerProfileEvent,
    ResourceControlAuthorization,
    ResourceCoordinationEvent,
    ResourceCoordinationSetting,
    Session,
    SettingValue,
    TaskModelProfile,
    TaskModelRun,
    User,
    Workspace,
    IdentityValidationSetting,
)
from app.secret_store import SecretStore
from app.task_contracts import TASK_DEFINITIONS, TASK_ROLES
from app.typed_settings import value_type


def now_ts() -> int:
    return int(time.time())


class UnitOfWork(AbstractContextManager):
    def __init__(self, session_factory, secret_store: SecretStore):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.session = None
        self.repo = None

    def __enter__(self):
        self.session = self.session_factory()
        self.repo = ApplicationRepository(self.session, self.secret_store)
        return self

    def __exit__(self, exc_type, exc, _traceback):
        try:
            if exc_type:
                self.session.rollback()
            else:
                self.session.commit()
        finally:
            self.session.close()
        return False


class ApplicationRepository:
    def __init__(self, session, secret_store: SecretStore):
        self.session = session
        self.secret_store = secret_store

    # Identity and sessions
    def user_count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(User)) or 0)

    def admin_count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(User).where(User.is_admin == 1)) or 0)

    def user_by_username(self, username: str):
        return self.session.scalar(select(User).where(User.username == username))

    def user(self, user_id: str):
        return self.session.get(User, user_id)

    def create_user(self, username: str, password_hash: str) -> User:
        stamp = now_ts()
        user = User(
            id=secrets.token_hex(8),
            username=username,
            password_hash=password_hash,
            is_admin=1 if self.user_count() == 0 else 0,
            created_at=stamp,
        )
        self.session.add(user)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError("username exists") from exc
        human = HumanPrincipal(
            id=secrets.token_hex(12),
            user_id=user.id,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(human)
        self.session.flush()
        self.session.add(
            OwnerProfile(
                human_id=human.id,
                revision=0,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        self.session.flush()
        return user

    def human_principal(self, user_id: str):
        return self.session.scalar(select(HumanPrincipal).where(HumanPrincipal.user_id == user_id))

    def owner_profile(self, human_id: str):
        return self.session.get(OwnerProfile, human_id)

    def save_owner_profile(self, human_id: str, values: dict):
        row = self.owner_profile(human_id)
        if not row:
            stamp = now_ts()
            row = OwnerProfile(human_id=human_id, revision=0, created_at=stamp, updated_at=stamp)
            self.session.add(row)
        for field, value in values.items():
            setattr(row, field, value)
        row.revision = int(row.revision or 0) + 1
        row.updated_at = now_ts()
        self.session.flush()
        return row

    def add_owner_profile_event(
        self,
        human_id: str,
        changed_fields: list[str],
        *,
        action: str = "updated",
    ):
        if action not in {"created", "updated", "cleared"}:
            raise ValueError("invalid owner profile event action")
        event = OwnerProfileEvent(
            id=secrets.token_hex(12),
            human_id=human_id,
            action=action,
            changed_fields_json=json.dumps(sorted(set(changed_fields)), separators=(",", ":")),
            created_at=now_ts(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def session_record(self, token: str):
        return self.session.execute(
            select(Session, User).join(User, User.id == Session.user_id).where(Session.token == token)
        ).first()

    def create_session(self, user_id: str, ttl_seconds: int) -> Session:
        stamp = now_ts()
        record = Session(
            token=secrets.token_hex(24),
            user_id=user_id,
            created_at=stamp,
            expires_at=stamp + ttl_seconds,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def delete_session(self, token: str) -> None:
        self.session.execute(delete(Session).where(Session.token == token))

    # Settings
    def settings(self, user_id: str) -> dict | None:
        row = self.session.get(AppSetting, user_id)
        if not row:
            return None
        values = self.session.scalars(
            select(SettingValue).where(SettingValue.user_id == user_id).order_by(SettingValue.key)
        ).all()
        if values:
            preferences = {}
            for value in values:
                try:
                    preferences[value.key] = json.loads(value.value_json)
                except (TypeError, ValueError):
                    continue
        else:
            try:
                preferences = json.loads(row.preferences_json or "{}")
            except (TypeError, ValueError):
                preferences = {}
        api_key = row.openai_api_key or ""
        if row.openai_api_key_encrypted:
            api_key = self.secret_store.decrypt(row.openai_api_key_encrypted)
        return {
            "user_id": row.user_id,
            "global_default_model": row.global_default_model,
            "default_memory_mode": "off" if row.default_memory_mode == "off" else "saved",
            "stt_provider": row.stt_provider or "disabled",
            "tts_provider": row.tts_provider or "disabled",
            "tts_format": row.tts_format or "wav",
            "openai_api_key": api_key,
            "openai_api_key_encrypted": row.openai_api_key_encrypted,
            "onboarding_done": bool(row.onboarding_done),
            "preferences": preferences if isinstance(preferences, dict) else {},
        }

    def save_settings(self, user_id: str, values: dict, preserve_secret: bool = False) -> dict:
        row = self.session.get(AppSetting, user_id)
        if not row:
            row = AppSetting(user_id=user_id)
            self.session.add(row)
        row.global_default_model = values.get("global_default_model")
        row.default_memory_mode = "off" if values.get("default_memory_mode") == "off" else "saved"
        row.stt_provider = values.get("stt_provider") or "disabled"
        row.tts_provider = values.get("tts_provider") or "disabled"
        row.tts_format = values.get("tts_format") or "wav"
        row.onboarding_done = int(bool(values.get("onboarding_done")))
        submitted_key = values.get("openai_api_key")
        if not preserve_secret and submitted_key:
            row.openai_api_key = None
            row.openai_api_key_encrypted = self.secret_store.encrypt(submitted_key)
        preferences = values.get("preferences") if isinstance(values.get("preferences"), dict) else {}
        row.preferences_json = json.dumps(preferences, separators=(",", ":"))
        self.session.execute(delete(SettingValue).where(SettingValue.user_id == user_id))
        stamp = now_ts()
        for key, value in sorted(preferences.items()):
            self.session.add(
                SettingValue(
                    user_id=user_id,
                    key=str(key)[:120],
                    value_type=value_type(value),
                    value_json=json.dumps(value, separators=(",", ":")),
                    updated_at=stamp,
                )
            )
        self.session.flush()
        return self.settings(user_id)

    # Shared provider resource coordination
    def resource_coordination_setting(self):
        row = self.session.get(ResourceCoordinationSetting, 1)
        if row:
            return row
        stamp = now_ts()
        row = ResourceCoordinationSetting(
            id=1,
            mode="disabled",
            reserve_vram_mb=1024,
            max_wait_seconds=300,
            poll_interval_seconds=2.0,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_resource_coordination_setting(self, values: dict):
        row = self.resource_coordination_setting()
        for field in ("mode", "reserve_vram_mb", "max_wait_seconds", "poll_interval_seconds"):
            if field in values:
                setattr(row, field, values[field])
        row.updated_at = now_ts()
        self.session.flush()
        return row

    def resource_control_authorization(self, provider: str, endpoint_fingerprint: str):
        return self.session.scalar(
            select(ResourceControlAuthorization).where(
                ResourceControlAuthorization.provider == provider,
                ResourceControlAuthorization.endpoint_fingerprint == endpoint_fingerprint,
            )
        )

    def save_resource_control_authorization(
        self,
        *,
        provider: str,
        endpoint_fingerprint: str,
        exclusive_control: bool,
        allow_release: bool,
        authorized_by_user_id: str,
    ):
        row = self.resource_control_authorization(provider, endpoint_fingerprint)
        stamp = now_ts()
        if not row:
            row = ResourceControlAuthorization(
                id=secrets.token_hex(12),
                provider=provider,
                endpoint_fingerprint=endpoint_fingerprint,
                created_at=stamp,
            )
            self.session.add(row)
        row.exclusive_control = int(bool(exclusive_control))
        row.allow_release = int(bool(allow_release))
        row.authorized_by_user_id = authorized_by_user_id
        row.updated_at = stamp
        self.session.flush()
        return row

    def add_resource_coordination_event(
        self,
        *,
        job_id: str | None,
        user_id: str | None,
        provider: str,
        endpoint_fingerprint: str,
        action: str,
        outcome: str,
        detail: dict | None = None,
    ):
        row = ResourceCoordinationEvent(
            id=secrets.token_hex(12),
            job_id=job_id,
            user_id=user_id,
            provider=provider,
            endpoint_fingerprint=endpoint_fingerprint,
            action=action,
            outcome=outcome,
            detail_json=json.dumps(detail or {}, separators=(",", ":"), default=str),
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def resource_coordination_events(self, limit: int = 100):
        return self.session.scalars(
            select(ResourceCoordinationEvent)
            .order_by(ResourceCoordinationEvent.created_at.desc(), ResourceCoordinationEvent.id.desc())
            .limit(max(1, min(500, int(limit))))
        ).all()

    # Platform task models
    def ensure_task_model_profiles(self, user_id: str):
        existing = {
            row.role: row
            for row in self.session.scalars(select(TaskModelProfile).where(TaskModelProfile.user_id == user_id)).all()
        }
        settings = self.session.get(AppSetting, user_id)
        initial_model = settings.global_default_model if settings else None
        stamp = now_ts()
        for role in TASK_ROLES:
            if role in existing:
                continue
            defaults = TASK_DEFINITIONS[role].default_profile()
            row = TaskModelProfile(
                id=secrets.token_hex(12),
                user_id=user_id,
                role=role,
                provider=defaults["provider"],
                model=initial_model or defaults["model"],
                fallback_provider=defaults["fallback_provider"],
                fallback_model=defaults["fallback_model"],
                enabled=int(defaults["enabled"]),
                max_input_tokens=defaults["max_input_tokens"],
                max_output_tokens=defaults["max_output_tokens"],
                timeout_seconds=defaults["timeout_seconds"],
                temperature=defaults["temperature"],
                fallback_policy=defaults["fallback_policy"],
                created_at=stamp,
                updated_at=stamp,
            )
            self.session.add(row)
            existing[role] = row
        self.session.flush()
        return [existing[role] for role in TASK_ROLES]

    def task_model_profiles(self, user_id: str):
        self.ensure_task_model_profiles(user_id)
        rows = self.session.scalars(select(TaskModelProfile).where(TaskModelProfile.user_id == user_id)).all()
        by_role = {row.role: row for row in rows}
        return [by_role[role] for role in TASK_ROLES if role in by_role]

    def task_model_profile(self, user_id: str, role: str):
        self.ensure_task_model_profiles(user_id)
        return self.session.scalar(
            select(TaskModelProfile).where(
                TaskModelProfile.user_id == user_id,
                TaskModelProfile.role == role,
            )
        )

    def save_task_model_profile(self, user_id: str, role: str, values: dict):
        row = self.task_model_profile(user_id, role)
        if not row:
            raise LookupError("task model profile not found")
        for field in (
            "provider",
            "model",
            "fallback_provider",
            "fallback_model",
            "max_input_tokens",
            "max_output_tokens",
            "timeout_seconds",
            "temperature",
            "fallback_policy",
        ):
            if field in values:
                setattr(row, field, values[field])
        if "enabled" in values:
            row.enabled = int(bool(values["enabled"]))
        row.updated_at = now_ts()
        self.session.flush()
        return row

    def add_task_model_run(
        self,
        *,
        user_id: str,
        role: str,
        chat_id: str | None,
        turn_id: str | None,
        requested_provider: str | None,
        requested_model: str | None,
        input_tokens_estimated: int,
    ):
        row = TaskModelRun(
            id=secrets.token_hex(12),
            user_id=user_id,
            role=role,
            chat_id=chat_id,
            turn_id=turn_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
            status="running",
            fallback_used=0,
            attempts_json="[]",
            input_tokens_estimated=input_tokens_estimated,
            started_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def task_model_run(self, user_id: str, run_id: str):
        return self.session.scalar(
            select(TaskModelRun).where(TaskModelRun.id == run_id, TaskModelRun.user_id == user_id)
        )

    def task_model_run_by_id(self, run_id: str):
        return self.session.get(TaskModelRun, run_id)

    def task_model_runs(self, user_id: str, *, role: str | None = None, limit: int = 50):
        query = select(TaskModelRun).where(TaskModelRun.user_id == user_id)
        if role:
            query = query.where(TaskModelRun.role == role)
        return self.session.scalars(
            query.order_by(TaskModelRun.started_at.desc(), TaskModelRun.id.desc()).limit(limit)
        ).all()

    # Media catalog and deterministic execution plans
    def media_catalog_setting(self, user_id: str):
        row = self.session.get(MediaCatalogSetting, user_id)
        if row:
            return row
        stamp = now_ts()
        row = MediaCatalogSetting(
            user_id=user_id,
            vram_budget_mb=10240,
            max_loras=4,
            legacy_imported=0,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

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

    # Workspaces and personas
    def workspace(self, user_id: str, workspace_id: str):
        return self.session.scalar(select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user_id))

    def workspaces(self, user_id: str):
        return self.session.scalars(
            select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.created_at)
        ).all()

    def create_workspace(self, user_id: str, name: str) -> Workspace:
        row = Workspace(id=secrets.token_hex(8), user_id=user_id, name=name.strip(), created_at=now_ts())
        self.session.add(row)
        self.session.flush()
        return row

    def delete_workspace(self, user_id: str, workspace_id: str) -> bool:
        row = self.workspace(user_id, workspace_id)
        if not row:
            return False
        personas = self.session.scalar(
            select(func.count())
            .select_from(PersonaWorkspaceLink)
            .where(PersonaWorkspaceLink.workspace_id == workspace_id)
        )
        if personas:
            raise ValueError("workspace not empty; remove personas first")
        self.session.delete(row)
        return True

    def persona(self, user_id: str, persona_id: str):
        return self.session.scalar(
            select(Persona)
            .join(PersonaWorkspaceLink, PersonaWorkspaceLink.persona_id == Persona.id)
            .join(Workspace, Workspace.id == PersonaWorkspaceLink.workspace_id)
            .where(Persona.id == persona_id, Workspace.user_id == user_id)
            .distinct()
        )

    def personas(self, user_id: str):
        return self.session.scalars(
            select(Persona)
            .join(PersonaWorkspaceLink, PersonaWorkspaceLink.persona_id == Persona.id)
            .join(Workspace, Workspace.id == PersonaWorkspaceLink.workspace_id)
            .where(Workspace.user_id == user_id)
            .order_by(Persona.created_at)
            .distinct()
        ).all()

    def persona_workspace_ids(self, persona_id: str) -> list[str]:
        return list(
            self.session.scalars(
                select(PersonaWorkspaceLink.workspace_id)
                .where(PersonaWorkspaceLink.persona_id == persona_id)
                .order_by(PersonaWorkspaceLink.workspace_id)
            ).all()
        )

    def save_persona(self, user_id: str, values: dict, persona_id: str | None = None) -> Persona:
        workspace_ids = [value for value in values.get("workspace_ids", []) if value]
        primary = values.get("workspace_id") or (workspace_ids[0] if workspace_ids else None)
        if primary and primary not in workspace_ids:
            workspace_ids.insert(0, primary)
        if not workspace_ids:
            raise ValueError("workspace_ids must include at least one workspace")
        for workspace_id in workspace_ids:
            if not self.workspace(user_id, workspace_id):
                raise LookupError("workspace not found")
        row = self.persona(user_id, persona_id) if persona_id else None
        if persona_id and not row:
            raise LookupError("persona not found")
        if not row:
            row = Persona(id=secrets.token_hex(8), workspace_id=primary, name="", created_at=now_ts())
            self.session.add(row)
        row.workspace_id = primary
        row.name = values.get("name", row.name)
        row.avatar_url = values.get("avatar_url", row.avatar_url)
        row.system_prompt = values.get("system_prompt", row.system_prompt)
        row.personality_details = values.get("personality_details", row.personality_details)
        if "traits" in values:
            row.traits_json = json.dumps(values.get("traits") or {})
        row.default_model = values.get("default_model", row.default_model)
        if "allow_image_sends" in values:
            row.allow_image_sends = int(bool(values["allow_image_sends"]))
        for field in (
            "preferred_voice",
            "preferred_tts_model",
            "preferred_tts_speed",
            "preferred_voice_openai",
            "preferred_tts_model_openai",
            "preferred_tts_speed_openai",
            "preferred_voice_local",
            "preferred_tts_model_local",
            "preferred_tts_speed_local",
        ):
            if field in values:
                setattr(row, field, values[field])
        self.session.flush()
        self.session.execute(delete(PersonaWorkspaceLink).where(PersonaWorkspaceLink.persona_id == row.id))
        for workspace_id in workspace_ids:
            self.session.add(PersonaWorkspaceLink(persona_id=row.id, workspace_id=workspace_id))
        self.session.flush()
        return row

    def delete_persona(self, user_id: str, persona_id: str) -> bool:
        row = self.persona(user_id, persona_id)
        if not row:
            return False
        self.session.delete(row)
        return True

    # Chats and messages
    def chat(self, user_id: str, chat_id: str):
        return self.session.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))

    def chat_binding(self, chat_id: str):
        return self.session.get(ChatBinding, chat_id)

    def chats(self, user_id: str, include_hidden: bool = False):
        query = select(Chat).where(Chat.user_id == user_id)
        if not include_hidden:
            query = query.where(Chat.hidden_in_ui == 0)
        return self.session.scalars(query.order_by(Chat.updated_at.desc())).all()

    def chats_by_ids(self, user_id: str, chat_ids: list[str]):
        if not chat_ids:
            return []
        return self.session.scalars(select(Chat).where(Chat.user_id == user_id, Chat.id.in_(chat_ids))).all()

    def active_jobs_for_chats(self, user_id: str, chat_ids: list[str]):
        if not chat_ids:
            return []
        return self.session.scalars(
            select(AsyncJob).where(
                AsyncJob.user_id == user_id,
                AsyncJob.chat_id.in_(chat_ids),
                AsyncJob.status.in_({"queued", "running"}),
            )
        ).all()

    def delete_chat(self, row: Chat) -> None:
        self.session.delete(row)

    def create_chat(self, user_id: str, values: dict) -> Chat:
        if "workspace_id" in values:
            raise ValueError("workspace_id is not accepted; use an explicit access_context")
        persona_id = values.get("persona_id")
        if not persona_id:
            raise ValueError("persona_id is required")
        persona = self.persona(user_id, persona_id)
        if not persona:
            raise LookupError("persona not found")

        context = values.get("access_context")
        if not isinstance(context, dict):
            raise ValueError("access_context is required")
        context_kind = str(context.get("kind") or "").strip().lower()
        workspace_id = context.get("workspace_id")
        if context_kind not in {"personal", "workspace"}:
            raise ValueError("access context must be personal or workspace")
        if context_kind == "personal":
            if workspace_id:
                raise ValueError("personal access context cannot include a workspace")
            workspace_id = None
        elif not workspace_id:
            raise ValueError("workspace access context requires workspace_id")
        workspace = self.workspace(user_id, workspace_id) if workspace_id else None
        if workspace_id and not workspace:
            raise LookupError("workspace not found")
        if workspace_id and workspace_id not in self.persona_workspace_ids(persona.id):
            raise LookupError("persona not found")
        human = self.human_principal(user_id)
        if not human:
            raise LookupError("human principal not found")
        stamp = now_ts()
        row = Chat(
            id=secrets.token_hex(8),
            user_id=user_id,
            workspace_id=workspace_id,
            persona_id=persona_id,
            model_override=values.get("model"),
            memory_mode="off" if values.get("memory_mode") == "off" else "saved",
            title=values.get("title") or "New chat",
            hidden_in_ui=0,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        self.session.add(
            ChatBinding(
                chat_id=row.id,
                human_id=human.id,
                persona_id=persona.id,
                context_kind=context_kind,
                workspace_id=workspace_id,
                binding_status="active",
                persona_name_snapshot=persona.name,
                workspace_name_snapshot=workspace.name if workspace else None,
                created_at=stamp,
            )
        )
        self.session.flush()
        return row

    def messages(self, chat_id: str, limit: int | None = None):
        query = select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id)
        if limit:
            query = query.limit(limit)
        return self.session.scalars(query).all()

    def message(self, message_id: str):
        return self.session.get(Message, message_id)

    def messages_before(self, chat_id: str, created_at: int):
        return self.session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.created_at < created_at)
            .order_by(Message.created_at, Message.id)
        ).all()

    def add_message(self, chat_id: str, role: str, text: str, created_at: int | None = None) -> Message:
        stamp = int(created_at or now_ts())
        latest = self.session.scalar(select(func.max(Message.created_at)).where(Message.chat_id == chat_id))
        if latest is not None:
            stamp = max(stamp, int(latest) + 1)
        row = Message(
            id=secrets.token_hex(8),
            chat_id=chat_id,
            role=role,
            text=text,
            created_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    # Memory
    def memories(
        self,
        user_id: str,
        scope: str | None = None,
        scope_id: str | None = None,
        statuses: set[str] | None = None,
        grant_type: str | None = None,
        grant_target_id: str | None = None,
    ):
        query = select(Memory).where(Memory.user_id == user_id)
        if scope:
            query = query.where(Memory.tier == scope)
        if scope_id is not None:
            query = query.where(Memory.tier_ref_id == scope_id)
        if statuses:
            query = query.where(Memory.status.in_(statuses))
        if grant_type or grant_target_id:
            grant_query = select(MemoryGrant.id).where(
                MemoryGrant.memory_id == Memory.id,
                MemoryGrant.revoked_at.is_(None),
            )
            if grant_type:
                grant_query = grant_query.where(MemoryGrant.grant_type == grant_type)
            if grant_target_id:
                if grant_type == "workspace":
                    grant_query = grant_query.where(MemoryGrant.workspace_id == grant_target_id)
                elif grant_type == "persona":
                    grant_query = grant_query.where(MemoryGrant.persona_id == grant_target_id)
                else:
                    grant_query = grant_query.where(
                        or_(
                            MemoryGrant.persona_id == grant_target_id,
                            MemoryGrant.workspace_id == grant_target_id,
                        )
                    )
            query = query.where(grant_query.exists())
        return self.session.scalars(query.order_by(Memory.updated_at.desc(), Memory.id.desc())).all()

    def relevant_memories(
        self,
        user_id: str,
        *,
        workspace_id: str | None = None,
        persona_id: str | None = None,
        chat_id: str,
        search_query: str | None = None,
        limit: int = 40,
    ):
        del workspace_id, persona_id
        chat = self.chat(user_id, chat_id)
        binding = self.chat_binding(chat_id) if chat else None
        human = self.human_principal(user_id)
        if (
            not chat
            or not binding
            or not human
            or binding.human_id != human.id
            or binding.binding_status != "active"
            or not binding.persona_id
        ):
            return []
        persona = self.persona(user_id, binding.persona_id)
        if not persona:
            return []
        if binding.context_kind == "workspace":
            if (
                not binding.workspace_id
                or not self.workspace(user_id, binding.workspace_id)
                or binding.workspace_id not in self.persona_workspace_ids(binding.persona_id)
            ):
                return []

        limit = min(100, max(1, int(limit)))
        stamp = now_ts()
        grant_access = select(MemoryGrant.id).where(
            MemoryGrant.memory_id == Memory.id,
            MemoryGrant.human_id == human.id,
            MemoryGrant.revoked_at.is_(None),
            or_(
                and_(
                    MemoryGrant.grant_type == "persona",
                    MemoryGrant.persona_id == binding.persona_id,
                ),
                and_(
                    binding.context_kind == "workspace",
                    MemoryGrant.grant_type == "workspace",
                    MemoryGrant.workspace_id == binding.workspace_id,
                    select(PersonaWorkspaceLink.persona_id)
                    .where(
                        PersonaWorkspaceLink.persona_id == binding.persona_id,
                        PersonaWorkspaceLink.workspace_id == binding.workspace_id,
                    )
                    .exists(),
                ),
            ),
        )
        recent = list(
            self.session.scalars(
                select(Memory)
                .join(MemoryRecord, MemoryRecord.memory_id == Memory.id)
                .where(
                    Memory.user_id == user_id,
                    Memory.status == "active",
                    MemoryRecord.human_id == human.id,
                    MemoryRecord.access_state == "grants",
                    MemoryRecord.validity_status == "current",
                    or_(
                        MemoryRecord.memory_type != "temporal",
                        MemoryRecord.valid_until.is_(None),
                        MemoryRecord.valid_until > stamp,
                    ),
                    or_(
                        MemoryRecord.memory_type != "stateful",
                        MemoryRecord.stateful_status == "active",
                    ),
                    grant_access.exists(),
                )
                .order_by(Memory.updated_at.desc(), Memory.id.desc())
                .limit(limit)
            ).all()
        )
        if not search_query:
            return recent

        workspace_grant = ""
        params = {
            "user_id": user_id,
            "human_id": human.id,
            "persona_id": binding.persona_id,
            "query": search_query,
            "limit": limit,
            "now": stamp,
        }
        if binding.context_kind == "workspace" and binding.workspace_id:
            params["workspace_id"] = binding.workspace_id
            workspace_grant = (
                " OR (g.grant_type='workspace' AND g.workspace_id=:workspace_id "
                "AND EXISTS (SELECT 1 FROM persona_workspace_links pw "
                "WHERE pw.persona_id=:persona_id AND pw.workspace_id=:workspace_id))"
            )
        matched_ids = list(
            self.session.scalars(
                sql_text(
                    "SELECT m.id FROM memory_fts "
                    "JOIN memories m ON m.id=memory_fts.memory_id "
                    "JOIN memory_records r ON r.memory_id=m.id "
                    "WHERE memory_fts MATCH :query AND m.user_id=:user_id AND m.status='active' "
                    "AND r.human_id=:human_id AND r.access_state='grants' "
                    "AND r.validity_status='current' "
                    "AND (r.memory_type!='temporal' OR r.valid_until IS NULL OR r.valid_until>:now) "
                    "AND (r.memory_type!='stateful' OR r.stateful_status='active') "
                    "AND EXISTS (SELECT 1 FROM memory_grants g "
                    "WHERE g.memory_id=m.id AND g.human_id=:human_id AND g.revoked_at IS NULL "
                    "AND ((g.grant_type='persona' AND g.persona_id=:persona_id)"
                    f"{workspace_grant})) "
                    "ORDER BY m.updated_at DESC,m.id DESC LIMIT :limit"
                ),
                params,
            ).all()
        )
        if not matched_ids:
            return recent
        matched_rows = list(
            self.session.scalars(select(Memory).where(Memory.user_id == user_id, Memory.id.in_(matched_ids))).all()
        )
        by_id = {row.id: row for row in matched_rows}
        ranked = [by_id[memory_id] for memory_id in matched_ids if memory_id in by_id]
        seen = set(matched_ids)
        ranked.extend(row for row in recent if row.id not in seen)
        return ranked[:limit]

    def memory(self, user_id: str, memory_id: str):
        return self.session.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))

    def memory_record(self, memory_id: str):
        return self.session.get(MemoryRecord, memory_id)

    def memory_origin(self, memory_id: str):
        return self.session.get(MemoryOrigin, memory_id)

    def active_memory_grants(self, memory_id: str):
        return self.session.scalars(
            select(MemoryGrant)
            .where(MemoryGrant.memory_id == memory_id, MemoryGrant.revoked_at.is_(None))
            .order_by(MemoryGrant.grant_type, MemoryGrant.persona_id, MemoryGrant.workspace_id, MemoryGrant.id)
        ).all()

    def memory_grant_events(self, user_id: str, memory_id: str):
        human = self.human_principal(user_id)
        if not human:
            return []
        return self.session.scalars(
            select(MemoryGrantEvent)
            .where(MemoryGrantEvent.memory_id == memory_id, MemoryGrantEvent.human_id == human.id)
            .order_by(MemoryGrantEvent.created_at.desc(), MemoryGrantEvent.id.desc())
        ).all()

    def validate_memory_grants(
        self,
        user_id: str,
        grants: list[dict],
        *,
        allow_empty: bool = False,
    ) -> list[dict]:
        if not grants and not allow_empty:
            raise ValueError("at least one persona or workspace grant is required")
        normalized = []
        seen = set()
        for value in grants:
            if not isinstance(value, dict):
                raise ValueError("invalid memory grant")
            grant_type = str(value.get("grant_type") or "").strip().lower()
            target_id = str(value.get("target_id") or "").strip()
            if grant_type not in {"persona", "workspace"} or not target_id:
                raise ValueError("invalid memory grant")
            key = (grant_type, target_id)
            if key in seen:
                continue
            seen.add(key)
            if grant_type == "persona":
                if not self.persona(user_id, target_id):
                    raise LookupError("persona not found")
            elif not self.workspace(user_id, target_id):
                raise LookupError("workspace not found")
            normalized.append({"grant_type": grant_type, "target_id": target_id})
        if not normalized and not allow_empty:
            raise ValueError("at least one persona or workspace grant is required")
        return normalized

    def create_memory_record(
        self,
        memory_id: str,
        *,
        human_id: str,
        lineage: str = "native_v3",
        access_state: str = "grants",
        memory_type: str = "durable",
        validity_status: str = "current",
        valid_until: int | None = None,
        stateful_status: str | None = None,
        last_confirmed_at: int | None = None,
    ):
        stamp = now_ts()
        row = MemoryRecord(
            memory_id=memory_id,
            human_id=human_id,
            lineage=lineage,
            access_state=access_state,
            memory_type=memory_type,
            validity_status=validity_status,
            valid_until=valid_until,
            stateful_status=stateful_status,
            last_confirmed_at=last_confirmed_at,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_memory_origin(
        self,
        memory_id: str,
        *,
        human_id: str,
        source_kind: str,
        source_chat_id: str | None = None,
        source_persona_id: str | None = None,
        source_workspace_id: str | None = None,
        source_message_id: str | None = None,
        source_turn_id: str | None = None,
        evidence: dict | None = None,
        provenance_status: str = "resolved",
        revision_of_memory_id: str | None = None,
    ):
        row = MemoryOrigin(
            memory_id=memory_id,
            human_id=human_id,
            source_kind=source_kind,
            source_chat_id=source_chat_id,
            source_persona_id=source_persona_id,
            source_workspace_id=source_workspace_id,
            source_message_id=source_message_id,
            source_turn_id=source_turn_id,
            evidence_json=json.dumps(evidence or {}, separators=(",", ":")),
            provenance_status=provenance_status,
            revision_of_memory_id=revision_of_memory_id,
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_memory_grant(
        self,
        memory_id: str,
        *,
        human_id: str,
        grant_type: str,
        target_id: str,
        grant_source: str,
        granted_by_human_id: str,
    ):
        stamp = now_ts()
        row = MemoryGrant(
            id=secrets.token_hex(12),
            memory_id=memory_id,
            human_id=human_id,
            grant_type=grant_type,
            persona_id=target_id if grant_type == "persona" else None,
            workspace_id=target_id if grant_type == "workspace" else None,
            grant_source=grant_source,
            granted_by_human_id=granted_by_human_id,
            granted_at=stamp,
            revoked_by_human_id=None,
            revoked_at=None,
        )
        self.session.add(row)
        self.session.flush()
        self._add_memory_grant_event(row, "granted", target_id, stamp)
        return row

    def _add_memory_grant_event(self, grant, action: str, target_id: str, created_at: int):
        event = MemoryGrantEvent(
            id=secrets.token_hex(12),
            memory_id=grant.memory_id,
            grant_id=grant.id,
            human_id=grant.human_id,
            action=action,
            grant_type=grant.grant_type,
            target_id=target_id,
            created_at=created_at,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def replace_memory_grants(self, user_id: str, memory_id: str, grants: list[dict]):
        memory = self.memory(user_id, memory_id)
        if not memory:
            raise LookupError("memory not found")
        human = self.human_principal(user_id)
        if not human:
            raise LookupError("human principal not found")
        desired = self.validate_memory_grants(user_id, grants, allow_empty=True)
        desired_keys = {(value["grant_type"], value["target_id"]) for value in desired}
        active = list(self.active_memory_grants(memory_id))
        active_by_key = {
            (
                row.grant_type,
                row.persona_id if row.grant_type == "persona" else row.workspace_id,
            ): row
            for row in active
        }
        stamp = now_ts()
        for key, row in active_by_key.items():
            if key in desired_keys:
                continue
            row.revoked_by_human_id = human.id
            row.revoked_at = stamp
            self._add_memory_grant_event(row, "revoked", str(key[1]), stamp)
        for grant_type, target_id in sorted(desired_keys - set(active_by_key)):
            self.add_memory_grant(
                memory_id,
                human_id=human.id,
                grant_type=grant_type,
                target_id=target_id,
                grant_source="owner",
                granted_by_human_id=human.id,
            )
        record = self.memory_record(memory_id)
        if record:
            record.access_state = "grants"
            record.updated_at = stamp
        self.session.flush()
        return self.active_memory_grants(memory_id)

    def sync_memory_grants_from_revision(
        self,
        user_id: str,
        *,
        source_memory_id: str,
        target_memory_id: str,
    ):
        """Copy one revision's active access set without re-resolving deleted targets."""

        source = self.memory(user_id, source_memory_id)
        target = self.memory(user_id, target_memory_id)
        if not source or not target:
            raise LookupError("memory not found")
        human = self.human_principal(user_id)
        source_record = self.memory_record(source_memory_id)
        target_record = self.memory_record(target_memory_id)
        source_origin = self.memory_origin(source_memory_id)
        target_origin = self.memory_origin(target_memory_id)
        if (
            not human
            or not source_record
            or not target_record
            or not source_origin
            or not target_origin
            or source_record.human_id != human.id
            or target_record.human_id != human.id
            or source_record.access_state != "grants"
            or target_record.access_state != "grants"
            or source_origin.provenance_status != "resolved"
            or target_origin.provenance_status != "resolved"
        ):
            raise LookupError("memory access metadata not found")

        source_grants = list(self.active_memory_grants(source_memory_id))
        desired_by_key = {
            (
                row.grant_type,
                row.persona_id if row.grant_type == "persona" else row.workspace_id,
            ): row
            for row in source_grants
        }
        target_grants = list(self.active_memory_grants(target_memory_id))
        target_by_key = {
            (
                row.grant_type,
                row.persona_id if row.grant_type == "persona" else row.workspace_id,
            ): row
            for row in target_grants
        }
        stamp = now_ts()
        for key, row in target_by_key.items():
            if key in desired_by_key:
                continue
            row.revoked_by_human_id = human.id
            row.revoked_at = stamp
            self._add_memory_grant_event(row, "revoked", str(key[1]), stamp)
        for key in sorted(set(desired_by_key) - set(target_by_key)):
            source_grant = desired_by_key[key]
            self.add_memory_grant(
                target_memory_id,
                human_id=human.id,
                grant_type=source_grant.grant_type,
                target_id=str(key[1]),
                grant_source=source_grant.grant_source,
                granted_by_human_id=human.id,
            )
        target_record.updated_at = stamp
        self.session.flush()
        return self.active_memory_grants(target_memory_id)

    def copy_memory_v3(self, old_memory_id: str, new_memory_id: str):
        old_record = self.memory_record(old_memory_id)
        old_origin = self.memory_origin(old_memory_id)
        if not old_record or not old_origin:
            raise LookupError("memory metadata not found")
        self.create_memory_record(
            new_memory_id,
            human_id=old_record.human_id,
            lineage=old_record.lineage,
            access_state=old_record.access_state,
            memory_type=old_record.memory_type,
            validity_status=old_record.validity_status,
            valid_until=old_record.valid_until,
            stateful_status=old_record.stateful_status,
            last_confirmed_at=old_record.last_confirmed_at,
        )
        self.create_memory_origin(
            new_memory_id,
            human_id=old_origin.human_id,
            source_kind="edit",
            source_chat_id=old_origin.source_chat_id,
            source_persona_id=old_origin.source_persona_id,
            source_workspace_id=old_origin.source_workspace_id,
            source_message_id=old_origin.source_message_id,
            source_turn_id=old_origin.source_turn_id,
            evidence={"revision_of": old_memory_id},
            provenance_status=old_origin.provenance_status,
            revision_of_memory_id=old_memory_id,
        )
        for grant in self.active_memory_grants(old_memory_id):
            target_id = grant.persona_id if grant.grant_type == "persona" else grant.workspace_id
            self.add_memory_grant(
                new_memory_id,
                human_id=grant.human_id,
                grant_type=grant.grant_type,
                target_id=target_id,
                grant_source=grant.grant_source,
                granted_by_human_id=grant.granted_by_human_id,
            )

    def v3_memory_duplicate(
        self,
        user_id: str,
        normalized_content: str,
        persona_id: str,
        *,
        excluding_id: str | None = None,
    ):
        human = self.human_principal(user_id)
        if not human:
            return None
        query = (
            select(Memory)
            .join(MemoryRecord, MemoryRecord.memory_id == Memory.id)
            .join(MemoryGrant, MemoryGrant.memory_id == Memory.id)
            .where(
                Memory.user_id == user_id,
                Memory.normalized_content == normalized_content,
                Memory.status.in_({"pending", "active"}),
                MemoryRecord.human_id == human.id,
                MemoryRecord.access_state == "grants",
                MemoryGrant.human_id == human.id,
                MemoryGrant.grant_type == "persona",
                MemoryGrant.persona_id == persona_id,
                MemoryGrant.revoked_at.is_(None),
            )
        )
        if excluding_id:
            query = query.where(Memory.id != excluding_id)
        return self.session.scalar(query.order_by(Memory.updated_at.desc()).limit(1))

    def memories_by_ids(self, user_id: str, memory_ids: list[str]):
        if not memory_ids:
            return []
        return self.session.scalars(select(Memory).where(Memory.user_id == user_id, Memory.id.in_(memory_ids))).all()

    def delete_memory(self, row: Memory) -> None:
        self.session.delete(row)

    def validate_memory_scope(self, user_id: str, scope: str, scope_id: str | None):
        if scope == "global":
            return None
        if scope not in {"workspace", "persona", "chat"}:
            raise ValueError("invalid memory scope")
        if not scope_id:
            raise ValueError(f"scope_id is required for {scope} memory")
        owned = {
            "workspace": lambda: self.workspace(user_id, scope_id),
            "persona": lambda: self.persona(user_id, scope_id),
            "chat": lambda: self.chat(user_id, scope_id),
        }[scope]()
        if not owned:
            raise LookupError(f"{scope} not found")
        return scope_id

    def live_memory_duplicate(
        self,
        user_id: str,
        scope: str,
        scope_id: str | None,
        normalized_content: str,
        *,
        excluding_id: str | None = None,
    ):
        query = select(Memory).where(
            Memory.user_id == user_id,
            Memory.tier == scope,
            Memory.normalized_content == normalized_content,
            Memory.status.in_({"pending", "active"}),
        )
        query = query.where(Memory.tier_ref_id.is_(None) if scope_id is None else Memory.tier_ref_id == scope_id)
        if excluding_id:
            query = query.where(Memory.id != excluding_id)
        return self.session.scalar(query.order_by(Memory.updated_at.desc()).limit(1))

    def create_memory(
        self,
        *,
        user_id: str,
        scope: str,
        scope_id: str | None,
        content: str,
        normalized_content: str,
        status: str,
        source_type: str,
        source_message_id: str | None = None,
        source_turn_id: str | None = None,
        confidence: float | None = None,
        supersedes_id: str | None = None,
        extractor_provider: str | None = None,
        extractor_model: str | None = None,
        extractor_version: str | None = None,
    ) -> Memory:
        stamp = now_ts()
        latest = self.session.scalar(select(func.max(Memory.created_at)).where(Memory.user_id == user_id))
        if latest is not None:
            stamp = max(stamp, int(latest) + 1)
        row = Memory(
            id=secrets.token_hex(12),
            user_id=user_id,
            tier=scope,
            tier_ref_id=scope_id,
            content=content,
            normalized_content=normalized_content,
            status=status,
            source_type=source_type,
            source_message_id=source_message_id,
            source_turn_id=source_turn_id,
            confidence=confidence,
            supersedes_id=supersedes_id,
            extractor_provider=extractor_provider,
            extractor_model=extractor_model,
            extractor_version=extractor_version,
            created_at=stamp,
            updated_at=stamp,
            reviewed_at=stamp if status == "active" else None,
            forgotten_at=None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_memory_event(
        self,
        row: Memory,
        action: str,
        *,
        from_status: str | None,
        to_status: str | None,
        related_memory_id: str | None = None,
        snapshot: dict | None = None,
    ) -> MemoryEvent:
        stamp = now_ts()
        latest = self.session.scalar(select(func.max(MemoryEvent.created_at)).where(MemoryEvent.memory_id == row.id))
        if latest is not None:
            stamp = max(stamp, int(latest) + 1)
        event = MemoryEvent(
            id=secrets.token_hex(12),
            user_id=row.user_id,
            memory_id=row.id,
            related_memory_id=related_memory_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            snapshot_json=json.dumps(snapshot or {}, separators=(",", ":")),
            created_at=stamp,
            undone_at=None,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def memory_events(self, user_id: str, memory_id: str):
        return self.session.scalars(
            select(MemoryEvent)
            .where(MemoryEvent.user_id == user_id, MemoryEvent.memory_id == memory_id)
            .order_by(MemoryEvent.created_at.desc(), MemoryEvent.id.desc())
        ).all()

    def latest_undoable_memory_event(self, user_id: str, memory_id: str):
        return self.session.scalar(
            select(MemoryEvent)
            .where(
                MemoryEvent.user_id == user_id,
                MemoryEvent.memory_id == memory_id,
                MemoryEvent.undone_at.is_(None),
                MemoryEvent.action.in_({"approved", "rejected", "forgotten", "edited"}),
            )
            .order_by(MemoryEvent.created_at.desc(), MemoryEvent.id.desc())
            .limit(1)
        )

    def archive_scope_memories(self, user_id: str, scope: str, scope_id: str) -> None:
        for row in self.memories(user_id, scope, scope_id, {"pending", "active"}):
            previous = row.status
            snapshot = {"reviewed_at": row.reviewed_at, "forgotten_at": row.forgotten_at}
            stamp = now_ts()
            row.status = "forgotten"
            row.updated_at = stamp
            row.reviewed_at = stamp
            row.forgotten_at = stamp
            self.add_memory_event(
                row,
                "scope_archived",
                from_status=previous,
                to_status="forgotten",
                snapshot=snapshot,
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

    # Durable jobs and turns
    def job(self, user_id: str, job_id: str):
        return self.session.scalar(select(AsyncJob).where(AsyncJob.id == job_id, AsyncJob.user_id == user_id))

    def job_by_id(self, job_id: str):
        return self.session.get(AsyncJob, job_id)

    def turn(self, user_id: str, turn_id: str):
        return self.session.scalar(
            select(ConversationTurn).where(
                ConversationTurn.id == turn_id,
                ConversationTurn.user_id == user_id,
            )
        )

    def turn_by_id(self, turn_id: str):
        return self.session.get(ConversationTurn, turn_id)

    def turns_for_chat(self, user_id: str, chat_id: str):
        return self.session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.user_id == user_id, ConversationTurn.chat_id == chat_id)
            .order_by(ConversationTurn.sequence_number)
        ).all()

    def add_turn(self, *, user_id: str, chat_id: str, message_id: str, provider: str, model: str):
        sequence = self.session.scalar(
            update(Chat)
            .where(Chat.id == chat_id, Chat.user_id == user_id)
            .values(last_turn_sequence=Chat.last_turn_sequence + 1)
            .returning(Chat.last_turn_sequence)
        )
        if sequence is None:
            raise LookupError("chat not found")
        row = ConversationTurn(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            user_message_id=message_id,
            assistant_message_id=None,
            sequence_number=int(sequence),
            provider=provider,
            model=model,
            status="queued",
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def latest_summary(self, user_id: str, chat_id: str):
        return self.session.scalar(
            select(ConversationSummary)
            .where(
                ConversationSummary.user_id == user_id,
                ConversationSummary.chat_id == chat_id,
            )
            .order_by(ConversationSummary.sequence_number.desc())
            .limit(1)
        )

    def add_summary(
        self,
        *,
        user_id: str,
        chat_id: str,
        previous_summary_id: str | None,
        through_message_id: str,
        provider: str,
        model: str,
        prompt_version: str,
        source_digest: str,
        source_message_count: int,
        content: str,
        estimated_tokens: int,
    ):
        sequence = (
            int(
                self.session.scalar(
                    select(func.max(ConversationSummary.sequence_number)).where(ConversationSummary.chat_id == chat_id)
                )
                or 0
            )
            + 1
        )
        row = ConversationSummary(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            previous_summary_id=previous_summary_id,
            sequence_number=sequence,
            through_message_id=through_message_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            source_digest=source_digest,
            source_message_count=source_message_count,
            content=content,
            estimated_tokens=estimated_tokens,
            created_at=now_ts(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_job(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        turn_id: str | None,
        kind: str,
        progress: str,
        capability_request_id: str | None = None,
    ):
        stamp = now_ts()
        row = AsyncJob(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            turn_id=turn_id,
            capability_request_id=capability_request_id,
            kind=kind,
            status="queued",
            cancel_requested=0,
            created_at=stamp,
            updated_at=stamp,
            progress=progress,
        )
        self.session.add(row)
        self.session.flush()
        return row

    # Permissioned capabilities
    def capability_request(self, user_id: str, request_id: str):
        return self.session.scalar(
            select(CapabilityRequest).where(
                CapabilityRequest.id == request_id,
                CapabilityRequest.user_id == user_id,
            )
        )

    def capability_request_by_id(self, request_id: str):
        return self.session.get(CapabilityRequest, request_id)

    def capability_requests(
        self,
        user_id: str,
        *,
        chat_id: str | None = None,
        turn_id: str | None = None,
        statuses: set[str] | None = None,
    ):
        query = select(CapabilityRequest).where(CapabilityRequest.user_id == user_id)
        if chat_id is not None:
            query = query.where(CapabilityRequest.chat_id == chat_id)
        if turn_id is not None:
            query = query.where(CapabilityRequest.turn_id == turn_id)
        if statuses:
            query = query.where(CapabilityRequest.status.in_(statuses))
        return self.session.scalars(query.order_by(CapabilityRequest.requested_at, CapabilityRequest.id)).all()

    def capability_requests_for_turn(self, turn_id: str):
        return self.session.scalars(
            select(CapabilityRequest)
            .where(CapabilityRequest.turn_id == turn_id)
            .order_by(CapabilityRequest.requested_at, CapabilityRequest.id)
        ).all()

    def job_for_capability(self, request_id: str):
        return self.session.scalar(select(AsyncJob).where(AsyncJob.capability_request_id == request_id))

    def add_capability_request(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        turn_id: str | None,
        capability_key: str,
        arguments: dict,
        status: str,
        permission_mode: str,
        idempotency_key: str,
        expires_at: int | None = None,
        retry_of_request_id: str | None = None,
    ):
        existing = self.session.scalar(
            select(CapabilityRequest).where(
                CapabilityRequest.user_id == user_id,
                CapabilityRequest.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
        row = CapabilityRequest(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            turn_id=turn_id,
            capability_key=capability_key,
            arguments_json=json.dumps(arguments, separators=(",", ":"), ensure_ascii=False),
            status=status,
            permission_mode="explicit" if permission_mode == "auto" else permission_mode,
            permission_mode_effective=permission_mode,
            idempotency_key=idempotency_key,
            requested_at=now_ts(),
            expires_at=expires_at,
            retry_of_request_id=retry_of_request_id,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def add_capability_event(
        self,
        request,
        action: str,
        *,
        from_status: str | None,
        to_status: str | None,
        detail: dict | None = None,
    ):
        stamp = now_ts()
        latest = self.session.scalar(
            select(func.max(CapabilityEvent.created_at)).where(CapabilityEvent.capability_request_id == request.id)
        )
        if latest is not None:
            stamp = max(stamp, int(latest) + 1)
        row = CapabilityEvent(
            id=secrets.token_hex(12),
            user_id=request.user_id,
            capability_request_id=request.id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            detail_json=json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False),
            created_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def capability_events(self, user_id: str, request_id: str):
        return self.session.scalars(
            select(CapabilityEvent)
            .where(
                CapabilityEvent.user_id == user_id,
                CapabilityEvent.capability_request_id == request_id,
            )
            .order_by(CapabilityEvent.created_at, CapabilityEvent.id)
        ).all()

    # Durable persona chat attachments
    def add_chat_attachment(
        self,
        *,
        user_id: str,
        chat_id: str,
        assistant_message_id: str,
        capability_request_id: str,
        kind: str,
        status: str,
    ):
        existing = self.chat_attachment_for_capability(user_id, capability_request_id)
        if existing:
            return existing
        stamp = now_ts()
        row = ChatAttachment(
            id=secrets.token_hex(12),
            user_id=user_id,
            chat_id=chat_id,
            assistant_message_id=assistant_message_id,
            capability_request_id=capability_request_id,
            kind=kind,
            status=status,
            identity_state="not_applicable",
            retry_available=0,
            created_at=stamp,
            updated_at=stamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def chat_attachment_for_capability(self, user_id: str, request_id: str):
        return self.session.scalar(
            select(ChatAttachment).where(
                ChatAttachment.user_id == user_id,
                ChatAttachment.capability_request_id == request_id,
            )
        )

    def chat_attachments(self, user_id: str, chat_id: str):
        return self.session.scalars(
            select(ChatAttachment)
            .where(ChatAttachment.user_id == user_id, ChatAttachment.chat_id == chat_id)
            .order_by(ChatAttachment.created_at, ChatAttachment.id)
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
