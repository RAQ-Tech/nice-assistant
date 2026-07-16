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
    ConversationTurn,
    ConversationSummary,
    MediaCatalogResource,
    MediaCatalogSetting,
    MediaExecutionPlan,
    MediaFile,
    MediaGenerationAttempt,
    MediaResourceCompatibility,
    Memory,
    MemoryEvent,
    Message,
    Persona,
    PersonaIdentityEvent,
    PersonaIdentityReference,
    PersonaIdentityValidation,
    PersonaVisualIdentity,
    PersonaWorkspaceLink,
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
        user = User(
            id=secrets.token_hex(8),
            username=username,
            password_hash=password_hash,
            is_admin=1 if self.user_count() == 0 else 0,
            created_at=now_ts(),
        )
        self.session.add(user)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError("username exists") from exc
        return user

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
        chats = self.session.scalar(
            select(func.count()).select_from(Chat).where(Chat.user_id == user_id, Chat.workspace_id == workspace_id)
        )
        if personas or chats:
            raise ValueError("workspace not empty; remove personas/chats first")
        self.archive_scope_memories(user_id, "workspace", workspace_id)
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
        self.archive_scope_memories(user_id, "persona", persona_id)
        for chat in self.session.scalars(select(Chat).where(Chat.user_id == user_id, Chat.persona_id == persona_id)):
            chat.persona_id = None
        self.session.delete(row)
        return True

    # Chats and messages
    def chat(self, user_id: str, chat_id: str):
        return self.session.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))

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
        workspace_id = values.get("workspace_id")
        persona_id = values.get("persona_id")
        if workspace_id and not self.workspace(user_id, workspace_id):
            raise LookupError("workspace not found")
        persona = self.persona(user_id, persona_id) if persona_id else None
        if persona_id and not persona:
            raise LookupError("persona not found")
        if persona and not workspace_id:
            workspace_id = persona.workspace_id
        if persona and workspace_id not in self.persona_workspace_ids(persona.id):
            raise LookupError("persona not found")
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
    ):
        query = select(Memory).where(Memory.user_id == user_id)
        if scope:
            query = query.where(Memory.tier == scope)
        if scope_id is not None:
            query = query.where(Memory.tier_ref_id == scope_id)
        if statuses:
            query = query.where(Memory.status.in_(statuses))
        return self.session.scalars(query.order_by(Memory.updated_at.desc(), Memory.id.desc())).all()

    def relevant_memories(
        self,
        user_id: str,
        *,
        workspace_id: str | None,
        persona_id: str | None,
        chat_id: str,
        search_query: str | None = None,
        limit: int = 40,
    ):
        scopes = [Memory.tier == "global"]
        if workspace_id:
            scopes.append(and_(Memory.tier == "workspace", Memory.tier_ref_id == workspace_id))
        if persona_id:
            scopes.append(and_(Memory.tier == "persona", Memory.tier_ref_id == persona_id))
        scopes.append(and_(Memory.tier == "chat", Memory.tier_ref_id == chat_id))
        limit = min(100, max(1, int(limit)))
        recent = list(
            self.session.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.status == "active",
                    or_(*scopes),
                )
                .order_by(Memory.updated_at.desc(), Memory.id.desc())
                .limit(limit)
            ).all()
        )
        if not search_query:
            return recent

        clauses = ["m.tier='global'"]
        params = {"user_id": user_id, "query": search_query, "limit": limit}
        if workspace_id:
            clauses.append("(m.tier='workspace' AND m.tier_ref_id=:workspace_id)")
            params["workspace_id"] = workspace_id
        if persona_id:
            clauses.append("(m.tier='persona' AND m.tier_ref_id=:persona_id)")
            params["persona_id"] = persona_id
        clauses.append("(m.tier='chat' AND m.tier_ref_id=:chat_id)")
        params["chat_id"] = chat_id
        matched_ids = list(
            self.session.scalars(
                sql_text(
                    "SELECT m.id FROM memory_fts "
                    "JOIN memories m ON m.id=memory_fts.memory_id "
                    "WHERE memory_fts MATCH :query AND m.user_id=:user_id AND m.status='active' "
                    f"AND ({' OR '.join(clauses)}) "
                    "ORDER BY bm25(memory_fts),m.updated_at DESC,m.id DESC LIMIT :limit"
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
