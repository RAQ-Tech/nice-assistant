from __future__ import annotations

import json
from pathlib import Path

from app.auth import hash_password, is_masked_secret, mask_secret, verify_password
from app.repositories import UnitOfWork, now_ts
from app.service_errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RequestError,
)


class AuthContext:
    def __init__(self, user_id: str, token: str, expires_at: int | None, is_admin: bool):
        self.user_id = user_id
        self.token = token
        self.expires_at = expires_at
        self.is_admin = is_admin


def workspace_response(row) -> dict:
    return {"id": row.id, "name": row.name, "created_at": row.created_at}


def persona_response(repo, row) -> dict:
    try:
        traits = json.loads(row.traits_json or "{}")
    except (TypeError, ValueError):
        traits = {}
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "workspace_ids": repo.persona_workspace_ids(row.id),
        "name": row.name,
        "avatar_url": row.avatar_url,
        "system_prompt": row.system_prompt,
        "personality_details": row.personality_details,
        "traits": traits,
        "default_model": row.default_model,
        "preferred_voice": row.preferred_voice,
        "preferred_tts_model": row.preferred_tts_model,
        "preferred_tts_speed": row.preferred_tts_speed,
        "preferred_voice_openai": row.preferred_voice_openai,
        "preferred_tts_model_openai": row.preferred_tts_model_openai,
        "preferred_tts_speed_openai": row.preferred_tts_speed_openai,
        "preferred_voice_local": row.preferred_voice_local,
        "preferred_tts_model_local": row.preferred_tts_model_local,
        "preferred_tts_speed_local": row.preferred_tts_speed_local,
        "created_at": row.created_at,
    }


def settings_response(row: dict | None) -> dict:
    row = row or {}
    return {
        "global_default_model": row.get("global_default_model"),
        "default_memory_mode": "off" if row.get("default_memory_mode") == "off" else "saved",
        "stt_provider": row.get("stt_provider") or "disabled",
        "tts_provider": row.get("tts_provider") or "disabled",
        "tts_format": row.get("tts_format") or "wav",
        "openai_api_key": mask_secret(row.get("openai_api_key")),
        "onboarding_done": bool(row.get("onboarding_done")),
        "preferences": row.get("preferences") or {},
    }


class ResourceService:
    def __init__(
        self,
        session_factory,
        secret_store,
        *,
        allow_public_signup: bool,
        session_ttl_seconds: int,
        password_hasher=hash_password,
        password_verifier=verify_password,
        persona_delete_hook=None,
        provider_url_policy=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.allow_public_signup = allow_public_signup
        self.session_ttl_seconds = session_ttl_seconds
        self.password_hasher = password_hasher
        self.password_verifier = password_verifier
        self.persona_delete_hook = persona_delete_hook
        self.provider_url_policy = provider_url_policy

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def create_user(self, username: str, password: str) -> dict:
        username = username.strip()
        if not username or len(password) < 8:
            raise RequestError("username and password of at least 8 characters are required")
        with self._uow() as uow:
            if uow.repo.user_count() and not self.allow_public_signup:
                raise AuthorizationError("Account creation is disabled after setup.")
            try:
                user = uow.repo.create_user(username, self.password_hasher(password))
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            return {"id": user.id}

    def login(self, username: str, password: str) -> tuple[AuthContext, dict]:
        with self._uow() as uow:
            user = uow.repo.user_by_username(username.strip())
            if not user or not self.password_verifier(password, user.password_hash):
                raise AuthenticationError("invalid credentials")
            session = uow.repo.create_session(user.id, self.session_ttl_seconds)
            context = AuthContext(user.id, session.token, session.expires_at, bool(user.is_admin))
            return context, {
                "user_id": user.id,
                "expires_at": session.expires_at,
                "ttl_seconds": self.session_ttl_seconds,
                "is_admin": bool(user.is_admin),
            }

    def authenticate(self, token: str | None) -> AuthContext:
        if not token:
            raise AuthenticationError()
        with self._uow() as uow:
            pair = uow.repo.session_record(token)
            if not pair:
                raise AuthenticationError()
            session, user = pair
            settings = uow.repo.settings(user.id) or {}
            auto_logout = bool((settings.get("preferences") or {}).get("general_auto_logout", True))
            stamp = now_ts()
            if auto_logout and session.expires_at and session.expires_at <= stamp:
                uow.repo.delete_session(token)
                raise AuthenticationError("session expired")
            if auto_logout:
                session.expires_at = stamp + self.session_ttl_seconds
            return AuthContext(user.id, token, session.expires_at, bool(user.is_admin))

    def logout(self, token: str) -> None:
        with self._uow() as uow:
            uow.repo.delete_session(token)

    def get_settings(self, user_id: str) -> dict:
        with self._uow() as uow:
            return settings_response(uow.repo.settings(user_id))

    def save_settings(self, user_id: str, values: dict) -> dict:
        preferences = dict(values.get("preferences") or {})
        for key, label in (
            ("tts_local_base_url", "Local speech service"),
            ("image_local_base_url", "Local image service"),
        ):
            if preferences.get(key) and self.provider_url_policy:
                try:
                    preferences[key] = self.provider_url_policy.normalize(preferences[key], label=label)
                except ValueError as exc:
                    raise RequestError(str(exc), 400) from exc
        values = dict(values)
        values["preferences"] = preferences
        with self._uow() as uow:
            current = uow.repo.settings(user_id) or {}
            submitted = values.get("openai_api_key")
            preserve = submitted is None or submitted == "" or is_masked_secret(submitted)
            if preserve:
                values = dict(values)
                values["openai_api_key"] = current.get("openai_api_key")
            saved = uow.repo.save_settings(user_id, values, preserve_secret=preserve)
            return settings_response(saved)

    def list_workspaces(self, user_id: str) -> list[dict]:
        with self._uow() as uow:
            return [workspace_response(row) for row in uow.repo.workspaces(user_id)]

    def create_workspace(self, user_id: str, name: str) -> dict:
        if not name.strip():
            raise RequestError("name required", 400)
        with self._uow() as uow:
            return workspace_response(uow.repo.create_workspace(user_id, name))

    def update_workspace(self, user_id: str, workspace_id: str, name: str) -> dict:
        if not name.strip():
            raise RequestError("name required", 400)
        with self._uow() as uow:
            row = uow.repo.workspace(user_id, workspace_id)
            if not row:
                raise NotFoundError("workspace not found")
            row.name = name.strip()
            return workspace_response(row)

    def delete_workspace(self, user_id: str, workspace_id: str) -> None:
        with self._uow() as uow:
            try:
                deleted = uow.repo.delete_workspace(user_id, workspace_id)
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            if not deleted:
                raise NotFoundError("workspace not found")

    def list_personas(self, user_id: str) -> list[dict]:
        with self._uow() as uow:
            return [persona_response(uow.repo, row) for row in uow.repo.personas(user_id)]

    def get_persona(self, user_id: str, persona_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.persona(user_id, persona_id)
            if not row:
                raise NotFoundError("persona not found")
            return persona_response(uow.repo, row)

    def save_persona(self, user_id: str, values: dict, persona_id: str | None = None) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.save_persona(user_id, values, persona_id)
                return persona_response(uow.repo, row)
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc

    def delete_persona(self, user_id: str, persona_id: str) -> None:
        cleanup = self.persona_delete_hook(user_id, persona_id) if self.persona_delete_hook else None
        with self._uow() as uow:
            if not uow.repo.delete_persona(user_id, persona_id):
                raise NotFoundError("persona not found")
        if cleanup:
            cleanup()

    def require_admin(self, context: AuthContext) -> None:
        if not context.is_admin:
            raise AuthorizationError("admin access required")

    def media_path(self, user_id: str, media_id: str) -> Path:
        with self._uow() as uow:
            row = uow.repo.media(user_id, media_id)
            if not row:
                raise NotFoundError()
            path = Path(row.local_path)
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise NotFoundError("missing file")
        return path

    def list_media(self, user_id: str, *, kind: str | None = None, limit: int = 100) -> list[dict]:
        with self._uow() as uow:
            rows = uow.repo.media_items(user_id, kind=kind, limit=limit)
            items = []
            for row in rows:
                path = Path(row.local_path)
                try:
                    available = path.is_file() and path.stat().st_size > 0
                except OSError:
                    available = False
                if not available:
                    continue
                items.append(
                    {
                        "id": row.id,
                        "chat_id": row.chat_id,
                        "kind": row.kind,
                        "filename": row.filename,
                        "content_url": f"/api/v1/media/{row.id}",
                        "created_at": row.created_at,
                    }
                )
            return items

    def legacy_media_path(self, user_id: str, kind: str, filename: str) -> Path:
        safe = Path(filename).name
        if safe != filename:
            raise NotFoundError()
        with self._uow() as uow:
            row = uow.repo.media_by_filename(user_id, kind, safe)
            if not row:
                raise NotFoundError()
            path = Path(row.local_path)
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise NotFoundError()
        return path

    def audio_path(self, user_id: str, audio_id: str) -> Path:
        with self._uow() as uow:
            row = uow.repo.audio(user_id, audio_id)
            if not row:
                raise NotFoundError()
            path = Path(row.local_path)
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise NotFoundError("missing file")
        return path
