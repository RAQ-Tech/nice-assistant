from __future__ import annotations

import json
from pathlib import Path

from app.auth import hash_password, is_masked_secret, mask_secret, verify_password
from app.persona_voice import parse as parse_voice_preferences
from app.context_policy import ContextPolicy, TokenEstimator
from app.owner_profile import owner_profile_tokens, profile_budget, profile_too_large_message
from app.persona_lore import (
    entry_from_row,
    fired_keys,
    matching_entries,
    parse_keys,
    scan_window,
    select_lore,
)
from app.persona_card import (
    CARD_FIELDS,
    CARD_STORED_FIELDS,
    CardBudget,
    card_budget,
    card_token_estimate,
    card_too_large_message,
    example_dialogue_fit,
)
from app.repositories import UnitOfWork, now_ts
from app.wyoming_client import WyomingUnavailable, parse_address as parse_wyoming_address
from app.settings import (
    normalize_media_preferences,
    validate_media_preferences,
    validate_pregeneration_preferences,
)
from app.service_errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PersonaCardTooLargeError,
    RequestError,
)


class AuthContext:
    def __init__(
        self,
        user_id: str,
        token: str,
        expires_at: int | None,
        is_admin: bool,
        auto_logout: bool,
    ):
        self.user_id = user_id
        self.token = token
        self.expires_at = expires_at
        self.is_admin = is_admin
        self.auto_logout = auto_logout


def workspace_response(row) -> dict:
    return {"id": row.id, "name": row.name, "created_at": row.created_at}


def persona_response(repo, row, budget: CardBudget) -> dict:
    try:
        traits = json.loads(row.traits_json or "{}")
    except (TypeError, ValueError):
        traits = {}
    authored, included, example_tokens = example_dialogue_fit(
        getattr(row, "card_example_dialogue", None), row.name, budget
    )
    return {
        "card_cap_tokens": budget.cap_tokens,
        "card_prompt_budget_tokens": budget.prompt_budget_tokens,
        "card_context_window_tokens": budget.context_window_tokens,
        "card_example_dialogue": getattr(row, "card_example_dialogue", None),
        "example_block_count": authored,
        "example_blocks_included": included,
        "example_token_estimate": example_tokens,
        "example_budget_tokens": budget.example_tokens,
        "id": row.id,
        "workspace_id": row.workspace_id,
        "workspace_ids": repo.persona_workspace_ids(row.id),
        "name": row.name,
        "avatar_url": row.avatar_url,
        "system_prompt": row.system_prompt,
        "personality_details": row.personality_details,
        **{field: getattr(row, field, None) for field in CARD_FIELDS},
        "card_token_estimate": int(row.card_token_estimate or 0),
        "traits": traits,
        "default_model": row.default_model,
        "allow_image_sends": bool(row.allow_image_sends),
        "voice_preferences": parse_voice_preferences(row.voice_preferences_json),
        "created_at": row.created_at,
    }


def lore_entry_response(row, budget: CardBudget) -> dict:
    return {
        "id": row.id,
        "persona_id": row.persona_id,
        "title": row.title,
        "keys": list(parse_keys(row.keys_json)),
        "secondary_keys": list(parse_keys(row.secondary_keys_json)),
        "content": row.content,
        "always_on": bool(row.always_on),
        "case_sensitive": bool(row.case_sensitive),
        "match_word_forms": bool(getattr(row, "match_word_forms", 1)),
        "priority": int(row.priority),
        "enabled": bool(row.enabled),
        "token_estimate": int(row.token_estimate or 0),
        "budget_tokens": budget.lore_tokens,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
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
        "preferences": normalize_media_preferences(row.get("preferences") or {}),
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
        media_catalog=None,
        context_policy: ContextPolicy | None = None,
    ):
        self.context_policy = context_policy or ContextPolicy()
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.allow_public_signup = allow_public_signup
        self.session_ttl_seconds = session_ttl_seconds
        self.password_hasher = password_hasher
        self.password_verifier = password_verifier
        self.persona_delete_hook = persona_delete_hook
        self.provider_url_policy = provider_url_policy
        self.media_catalog = media_catalog

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
            settings = uow.repo.settings(user.id) or {}
            auto_logout = bool((settings.get("preferences") or {}).get("general_auto_logout", True))
            context = AuthContext(
                user.id,
                session.token,
                session.expires_at,
                bool(user.is_admin),
                auto_logout,
            )
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
            return AuthContext(user.id, token, session.expires_at, bool(user.is_admin), auto_logout)

    def logout(self, token: str) -> None:
        with self._uow() as uow:
            uow.repo.delete_session(token)

    def get_settings(self, user_id: str) -> dict:
        with self._uow() as uow:
            return settings_response(uow.repo.settings(user_id))

    def save_settings(self, user_id: str, values: dict) -> dict:
        preferences = normalize_media_preferences(values.get("preferences") or {})
        for key, label in (
            ("tts_local_base_url", "Local speech service"),
            ("stt_local_base_url", "Local transcription service"),
            ("image_local_base_url", "Local image service"),
        ):
            if preferences.get(key) and self.provider_url_policy:
                try:
                    preferences[key] = self.provider_url_policy.normalize(preferences[key], label=label)
                except ValueError as exc:
                    raise RequestError(str(exc), 400) from exc
        # Wyoming is a socket rather than a URL, so the policy is given one
        # built from its host. Letting it past because the protocol is unusual
        # would leave one local provider able to reach the internet while every
        # other one cannot, under a label that says it does not.
        if preferences.get("stt_wyoming_address"):
            try:
                host, port = parse_wyoming_address(preferences["stt_wyoming_address"])
            except WyomingUnavailable as exc:
                raise RequestError(str(exc), 400) from exc
            if self.provider_url_policy:
                try:
                    self.provider_url_policy.normalize(f"http://{host}:{port}", label="Local transcription service")
                except ValueError as exc:
                    raise RequestError(str(exc), 400) from exc
            preferences["stt_wyoming_address"] = f"{host}:{port}"
        values = dict(values)
        values["preferences"] = preferences
        with self._uow() as uow:
            current = uow.repo.settings(user_id) or {}
            previous_preferences = normalize_media_preferences(current.get("preferences") or {})
            validate_media_preferences(preferences, previous_preferences)
            # A schedule that could never fire is refused when it is saved,
            # rather than corrected into something the owner did not choose.
            validate_pregeneration_preferences(preferences)
            # Protected material fails a turn rather than degrading, so it is bounded here.
            estimate = owner_profile_tokens(preferences)
            budget = profile_budget(preferences, self.context_policy)
            if estimate > budget.cap_tokens:
                raise RequestError(profile_too_large_message(estimate, budget), 422)
            submitted = values.get("openai_api_key")
            preserve = submitted is None or submitted == "" or is_masked_secret(submitted)
            if preserve:
                values = dict(values)
                values["openai_api_key"] = current.get("openai_api_key")
            saved = uow.repo.save_settings(user_id, values, preserve_secret=preserve)
            if self.media_catalog:
                self.media_catalog.seed_newly_enabled_defaults(
                    uow.repo,
                    user_id,
                    previous_preferences,
                    preferences,
                )
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
            budget = self._card_budget(uow.repo, user_id)
            return [persona_response(uow.repo, row, budget) for row in uow.repo.personas(user_id)]

    def get_persona(self, user_id: str, persona_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.persona(user_id, persona_id)
            if not row:
                raise NotFoundError("persona not found")
            return persona_response(uow.repo, row, self._card_budget(uow.repo, user_id))

    def save_persona(self, user_id: str, values: dict, persona_id: str | None = None) -> dict:
        try:
            with self._uow() as uow:
                row = uow.repo.save_persona(user_id, values, persona_id)
                return persona_response(uow.repo, row, self._card_budget(uow.repo, user_id))
        except LookupError as exc:
            raise NotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise RequestError(str(exc), 400) from exc

    def save_persona_card(self, user_id: str, persona_id: str, values: dict) -> dict:
        """Reject a card that cannot fit instead of letting the turn fail on it later."""

        values = {field: str(values.get(field) or "").strip() for field in CARD_STORED_FIELDS}
        # Only the always-present card is capped; example dialogue is clipped at turn time.
        estimate = card_token_estimate(values)
        with self._uow() as uow:
            budget = self._card_budget(uow.repo, user_id)
            if estimate > budget.cap_tokens:
                raise PersonaCardTooLargeError(card_too_large_message(estimate, budget))
            try:
                row = uow.repo.save_persona_card(user_id, persona_id, values, estimate)
            except LookupError as exc:
                raise NotFoundError(str(exc)) from exc
            return persona_response(uow.repo, row, budget)

    def list_persona_lore(self, user_id: str, persona_id: str) -> list[dict]:
        with self._uow() as uow:
            if not uow.repo.persona(user_id, persona_id):
                raise NotFoundError("persona not found")
            budget = self._card_budget(uow.repo, user_id)
            return [lore_entry_response(row, budget) for row in uow.repo.persona_lore_entries(user_id, persona_id)]

    def save_persona_lore(self, user_id: str, persona_id: str, values: dict, entry_id: str | None = None) -> dict:
        values = self._validated_lore(values)
        with self._uow() as uow:
            budget = self._card_budget(uow.repo, user_id)
            try:
                row = uow.repo.save_persona_lore_entry(
                    user_id,
                    persona_id,
                    values,
                    TokenEstimator.text(values["content"]),
                    entry_id,
                )
            except LookupError as exc:
                raise NotFoundError(str(exc)) from exc
            return lore_entry_response(row, budget)

    def copyable_persona_lore(self, user_id: str, persona_id: str) -> list[dict]:
        """What this persona could take, grouped by who has it.

        Only personas sharing a workspace appear, and only entries this persona
        does not already have by that title - offering somebody a copy of
        something they already copied is how a lore list fills up with
        duplicates nobody meant to make.
        """

        with self._uow() as uow:
            if not uow.repo.persona(user_id, persona_id):
                raise NotFoundError("persona not found")
            workspaces = set(uow.repo.persona_workspace_ids(persona_id))
            existing = {row.title.strip().casefold() for row in uow.repo.persona_lore_entries(user_id, persona_id)}
            groups = []
            for persona in uow.repo.personas(user_id):
                if persona.id == persona_id or not workspaces & set(uow.repo.persona_workspace_ids(persona.id)):
                    continue
                entries = [
                    {
                        "id": row.id,
                        "title": row.title,
                        "always_on": bool(row.always_on),
                        "token_estimate": row.token_estimate,
                    }
                    for row in uow.repo.persona_lore_entries(user_id, persona.id)
                    if row.title.strip().casefold() not in existing
                ]
                if entries:
                    groups.append({"persona_id": persona.id, "persona_name": persona.name, "entries": entries})
            return groups

    def copy_persona_lore(self, user_id: str, persona_id: str, source_entry_id: str) -> dict:
        """Take a copy of another persona's entry. A copy, not a link.

        Shared worldbuilding is the ordinary case - two personas in the same
        setting want the same facts about it - and retyping it is how the second
        one ends up subtly different from the first.

        What this does not do is follow the original. An entry that changed
        under a persona because somebody edited a different persona would be a
        surprise every time; the browser says so at the moment of copying, which
        is the only moment anybody is thinking about it.
        """

        with self._uow() as uow:
            target = uow.repo.persona(user_id, persona_id)
            if not target:
                raise NotFoundError("persona not found")
            source = uow.repo.persona_lore_entry(user_id, source_entry_id)
            if not source:
                raise NotFoundError("lore entry not found")
            if source.persona_id == persona_id:
                raise RequestError("That entry already belongs to this persona.", 400)
            self._require_shared_workspace(uow.repo, source.persona_id, persona_id)
            budget = self._card_budget(uow.repo, user_id)
            row = uow.repo.save_persona_lore_entry(
                user_id,
                persona_id,
                {
                    "title": source.title,
                    "keys": json.loads(source.keys_json or "[]"),
                    "secondary_keys": json.loads(source.secondary_keys_json or "[]"),
                    "content": source.content,
                    "always_on": bool(source.always_on),
                    "case_sensitive": bool(source.case_sensitive),
                    "match_word_forms": bool(source.match_word_forms),
                    "priority": source.priority,
                    "enabled": bool(source.enabled),
                },
                # Recomputed rather than copied. A stale estimate on the source
                # would otherwise be duplicated into a budget that reports it.
                TokenEstimator.text(source.content),
            )
            return lore_entry_response(row, budget)

    @staticmethod
    def _require_shared_workspace(repo, source_persona_id: str, target_persona_id: str) -> None:
        """Copying is bounded by the workspace, because that is what a workspace is for.

        A workspace is how somebody keeps unrelated work apart. Reaching across
        one to pull in an entry would make that separation advisory.
        """

        shared = set(repo.persona_workspace_ids(source_persona_id)) & set(repo.persona_workspace_ids(target_persona_id))
        if not shared:
            raise RequestError("Lore can only be copied between personas in the same workspace.", 400)

    def delete_persona_lore(self, user_id: str, persona_id: str, entry_id: str) -> None:
        with self._uow() as uow:
            existing = uow.repo.persona_lore_entry(user_id, entry_id)
            # The path names a persona, so it has to be that persona's entry.
            if not existing or existing.persona_id != persona_id:
                raise NotFoundError("lore entry not found")
            uow.repo.delete_persona_lore_entry(user_id, entry_id)

    def preview_persona_lore(self, user_id: str, persona_id: str, text: str) -> dict:
        """Show which entries a message fires. Without this, keyword tuning is guesswork."""

        with self._uow() as uow:
            if not uow.repo.persona(user_id, persona_id):
                raise NotFoundError("persona not found")
            budget = self._card_budget(uow.repo, user_id)
            rows = uow.repo.persona_lore_entries(user_id, persona_id, enabled_only=True)
            entries = [entry_from_row(row) for row in rows]
            included = select_lore(entries, text, [], budget.lore_tokens)
            included_ids = {entry.id for entry in included}
            fired = matching_entries(entries, scan_window(text, []))
            return {
                "budget_tokens": budget.lore_tokens,
                "used_tokens": sum(TokenEstimator.text(entry.content) + 3 for entry in included),
                "items": [
                    {
                        "id": entry.id,
                        "title": entry.title,
                        "always_on": entry.always_on,
                        "fired_keys": list(fired_keys(entry, scan_window(text, []))),
                        "priority": entry.priority,
                        "token_estimate": TokenEstimator.text(entry.content),
                        "included": entry.id in included_ids,
                    }
                    for entry in fired
                ],
            }

    @staticmethod
    def _validated_lore(values: dict) -> dict:
        title = str(values.get("title") or "").strip()
        content = str(values.get("content") or "").strip()
        if not title:
            raise RequestError("A lore entry needs a title so you can find it later.", 400)
        if not content:
            raise RequestError("A lore entry needs content to inject.", 400)
        keys = parse_keys(values.get("keys"))
        always_on = bool(values.get("always_on"))
        if not always_on and not keys:
            raise RequestError(
                "A lore entry needs at least one keyword, or turn on 'always include' instead.",
                400,
            )
        priority = values.get("priority")
        priority = 50 if priority is None else int(priority)
        if not 0 <= priority <= 100:
            raise RequestError("Priority must be between 0 and 100.", 400)
        return {
            "title": title,
            "content": content,
            "keys": list(keys),
            "secondary_keys": list(parse_keys(values.get("secondary_keys"))),
            "always_on": always_on,
            "case_sensitive": bool(values.get("case_sensitive")),
            "match_word_forms": True
            if values.get("match_word_forms") is None
            else bool(values.get("match_word_forms")),
            "priority": priority,
            "enabled": True if values.get("enabled") is None else bool(values.get("enabled")),
        }

    def _card_budget(self, repo, user_id: str) -> CardBudget:
        preferences = (repo.settings(user_id) or {}).get("preferences") or {}
        return card_budget(preferences, self.context_policy)

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
