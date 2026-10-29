from __future__ import annotations

import json

from sqlalchemy import select

from app.chat import (
    chat_title_needs_autogeneration,
    generate_chat_title,
    generate_chat_title_from_first_user_message,
    persona_instruction_block,
)
from app.capability_service import attachment_response
from app.chat_photos import PHOTO_MIME_TYPE, NormalizedPhoto, normalize_chat_photo, photo_from_data_url
from app.job_service import TERMINAL_STATES, JobService, turn_response
from app.context_service import ContextService
from app.memory_service import MemoryService
from app.persona_card import CARD_FIELDS
from app.owner_profile import render_owner_profile
from app.persona_lore import entry_from_row
from app.models import AsyncJob
from app.persona_output import (
    safe_persona_output_text,
)
from app.provider_registry import ProviderRegistry
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.task_contracts import (
    is_high_confidence_image_action_request,
)
from app.turn_events import TurnEventBroker
from app.turn_pipeline import TurnContext, TurnPipeline


# One provider serves persona conversation. Named rather than repeated.
PERSONA_PROVIDER = "ollama"
# A picture for a reply still on its way; the exchange cannot be forgotten under it.
UNFINISHED_PICTURE_STATES = {"pending_confirmation", "queued", "running"}


def _persona_mapping(persona):
    if not persona:
        return None
    return {
        "name": persona.name,
        "traits_json": persona.traits_json,
        "personality_details": persona.personality_details,
        "system_prompt": persona.system_prompt,
        **{field: getattr(persona, field, None) for field in CARD_FIELDS},
    }


def _chat_response(chat) -> dict:
    return {
        "id": chat.id,
        "workspace_id": chat.workspace_id,
        "persona_id": chat.persona_id,
        "model_override": chat.model_override,
        "memory_mode": chat.memory_mode,
        "title": chat.title,
        "hidden_in_ui": bool(chat.hidden_in_ui),
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


def _reject_rebinding(values: dict, chat) -> None:
    """Refuse any attempt to move a chat to another persona or workspace.

    Repeating the values a chat is already bound to stays acceptable, because
    the browser and the published API both still send them. Sending different
    ones is refused here, before a message, turn, job, or chat row is written,
    so a rejected request changes nothing at all.
    """

    for field, bound in (("persona_id", chat.persona_id), ("workspace_id", chat.workspace_id)):
        requested = values.get(field)
        if requested and requested != bound:
            raise ConflictError(
                "This conversation is bound to the persona and workspace it was created with. "
                "Start a new chat to use a different one."
            )


class ConversationService:
    def __init__(
        self,
        session_factory,
        secret_store,
        providers: ProviderRegistry,
        jobs: JobService,
        broker: TurnEventBroker,
        generation_timeout_seconds: float,
        context: ContextService,
        memory: MemoryService,
        capabilities,
        task_models,
        metrics=None,
        logger=None,
    ):
        self.metrics = metrics
        self.logger = logger
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.providers = providers
        self.jobs = jobs
        self.broker = broker
        self.generation_timeout_seconds = generation_timeout_seconds
        self.context = context
        self.memory = memory
        self.capabilities = capabilities
        self.task_models = task_models

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def list_chats(self, user_id: str) -> list[dict]:
        with self._uow() as uow:
            return [_chat_response(chat) for chat in uow.repo.chats(user_id)]

    def create_chat(self, user_id: str, values: dict) -> dict:
        with self._uow() as uow:
            try:
                chat = uow.repo.create_chat(user_id, values)
            except LookupError as exc:
                raise NotFoundError(str(exc)) from exc
            return _chat_response(chat)

    def get_chat(self, user_id: str, chat_id: str) -> dict | None:
        with self._uow() as uow:
            chat = uow.repo.chat(user_id, chat_id)
            if not chat:
                return None
            attachments: dict[str, list] = {}
            for row in uow.repo.chat_attachments(user_id, chat_id):
                attachments.setdefault(row.assistant_message_id, []).append(
                    attachment_response(row, uow.repo.attachment_frames(row.id))
                )
            # A reply produced with reduced context stays explainable after a reload, so the
            # reason travels with the message it produced rather than only with the live turn.
            degraded = {
                turn.assistant_message_id: turn.context_degraded_reason
                for turn in uow.repo.turns_for_chat(user_id, chat_id)
                if turn.assistant_message_id and turn.context_degraded_reason
            }
            rows = uow.repo.messages(chat_id)
            photos = uow.repo.photos_for_messages(row.id for row in rows)
            messages = [
                {
                    "id": row.id,
                    "role": row.role,
                    "text": safe_persona_output_text(row.text) if row.role == "assistant" else row.text,
                    "created_at": row.created_at,
                    "attachments": attachments.get(row.id, []),
                    "degraded_reason": degraded.get(row.id),
                    "photos": [
                        {
                            "id": photo.id,
                            "url": f"/api/v1/photos/{photo.id}",
                            "width": photo.width,
                            "height": photo.height,
                        }
                        for photo in photos.get(row.id, [])
                    ],
                }
                for row in rows
            ]
            return {"chat": _chat_response(chat), "messages": messages}

    def update_chat(self, user_id: str, chat_id: str, values: dict) -> dict | None:
        with self._uow() as uow:
            chat = uow.repo.chat(user_id, chat_id)
            if not chat:
                return None
            # ADR 0032: retargeting a chat that already has a transcript would
            # leave the previous persona's replies in the next prompt. Selecting
            # a different persona starts a new chat instead.
            _reject_rebinding(values, chat)
            for field in ("title", "model_override", "memory_mode"):
                if field in values:
                    setattr(chat, field, self._memory_mode(values[field]) if field == "memory_mode" else values[field])
            if "hidden_in_ui" in values:
                chat.hidden_in_ui = int(bool(values["hidden_in_ui"]))
            chat.updated_at = now_ts()
            return _chat_response(chat)

    def hide_chat(self, user_id: str, chat_id: str) -> bool:
        with self._uow() as uow:
            chat = uow.repo.chat(user_id, chat_id)
            if not chat:
                return False
            chat.hidden_in_ui = 1
            chat.updated_at = now_ts()
            return True

    def delete_chat(self, user_id: str, chat_id: str) -> bool:
        with self._uow() as uow:
            chat = uow.repo.chat(user_id, chat_id)
            if not chat:
                return False
            if uow.repo.active_jobs_for_chats(user_id, [chat_id]):
                raise ConflictError("Cancel active work before permanently deleting this chat.")
            uow.repo.delete_chat(chat)
            return True

    def bulk_chat_action(self, user_id: str, action: str, chat_ids: list[str]) -> dict:
        ids = self._bulk_ids(chat_ids)
        with self._uow() as uow:
            rows = uow.repo.chats_by_ids(user_id, ids)
            if len(rows) != len(ids):
                raise NotFoundError("One or more chats were not found.")
            if action == "hide":
                stamp = now_ts()
                affected = 0
                for row in rows:
                    if not row.hidden_in_ui:
                        row.hidden_in_ui = 1
                        row.updated_at = stamp
                        affected += 1
            elif action == "delete":
                if uow.repo.active_jobs_for_chats(user_id, ids):
                    raise ConflictError("Cancel active work before permanently deleting the selected chats.")
                for row in rows:
                    uow.repo.delete_chat(row)
                affected = len(rows)
            else:
                raise RequestError("invalid chat bulk action", 400)
            return {
                "action": action,
                "requested_count": len(ids),
                "affected_count": affected,
                "ids": ids,
            }

    @staticmethod
    def _bulk_ids(values: list[str]) -> list[str]:
        ids = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if not ids:
            raise RequestError("At least one item must be selected.", 400)
        return ids

    def create_turn(self, user_id: str, chat_id: str, values: dict) -> tuple[dict, dict]:
        """Resolve a turn, make it durable, and hand it to the pipeline.

        Everything after the transaction commits lives in `TurnPipeline`. This
        function decides what the turn is; that object decides what happens to
        it.
        """

        text = str(values.get("text") or "").strip()
        # Decoded and re-encoded before the transaction, not inside it.
        photos = [normalize_chat_photo(photo_from_data_url(values["photo"]))] if values.get("photo") else []
        if not text and not photos:
            raise RequestError("text required", 400)
        with self._uow() as uow:
            ctx, turn_payload, job_payload = self._open_turn(uow.repo, user_id, chat_id, text, values, photos)
        self._start(ctx, user_id, chat_id)
        return turn_payload, job_payload

    def photo(self, user_id: str, photo_id: str):
        with self._uow() as uow:
            return uow.repo.message_photo(user_id, photo_id)

    def again(self, user_id: str, chat_id: str, message_id: str, values: dict) -> tuple[dict, dict]:
        """Answer the latest message again: a retry, a new reply, or an edit.

        The latest exchange is forgotten and its words sent again as a new turn -
        the same words, or the new ones somebody corrected them to - in one
        transaction, so a refused request leaves the conversation as it was.
        Only the latest: answering an earlier message again would leave every
        reply after it answering something that was no longer said.
        """

        with self._uow() as uow:
            turn = self._settled_exchange(uow.repo, user_id, chat_id, message_id)
            turns = uow.repo.turns_for_chat(user_id, chat_id)
            if turns and turns[-1].id != turn.id:
                raise ConflictError("Only the latest message can be answered again.")
            said = uow.repo.message(turn.user_message_id)
            text = str(values.get("text") or "").strip() or (said.text if said else "")
            # A photo sent with the message goes with it into the new turn.
            photos = [
                NormalizedPhoto(row.data, row.width, row.height)
                for row in uow.repo.photos_for_messages([turn.user_message_id]).get(turn.user_message_id, [])
            ]
            if not text and not photos:
                raise RequestError("text required", 400)
            unfinished = self._forget_exchange(uow, chat_id, turn)
            ctx, turn_payload, job_payload = self._open_turn(uow.repo, user_id, chat_id, text, values, photos)
        self._cancel_followups(user_id, unfinished)
        self._start(ctx, user_id, chat_id)
        return turn_payload, job_payload

    def delete_exchange(self, user_id: str, chat_id: str, message_id: str) -> None:
        """Delete a message and the reply it belongs with, for good.

        A message and its reply are one exchange: deleting only one would leave
        a reply to nothing, or a question the next prompt answers twice. A
        message no turn claims - an older transcript - goes on its own.
        """

        with self._uow() as uow:
            if not uow.repo.chat(user_id, chat_id):
                raise NotFoundError("chat not found")
            turn = uow.repo.turn_for_message(user_id, chat_id, message_id)
            if turn:
                self._require_settled(uow.repo, turn)
                unfinished = self._forget_exchange(uow, chat_id, turn)
            else:
                message = uow.repo.message(message_id)
                if not message or message.chat_id != chat_id:
                    raise NotFoundError("message not found")
                uow.repo.drop_summaries_from(chat_id, message.created_at)
                uow.repo.delete_messages([message.id])
                unfinished = []
        self._cancel_followups(user_id, unfinished)

    def _settled_exchange(self, repo, user_id: str, chat_id: str, message_id: str):
        if not repo.chat(user_id, chat_id):
            raise NotFoundError("chat not found")
        turn = repo.turn_for_message(user_id, chat_id, message_id)
        if not turn:
            raise NotFoundError("message not found")
        self._require_settled(repo, turn)
        return turn

    @staticmethod
    def _require_settled(repo, turn) -> None:
        if turn.status not in TERMINAL_STATES:
            raise ConflictError("This reply is still being written. Stop it first, or wait for it.")
        if any(row.status in UNFINISHED_PICTURE_STATES for row in repo.capability_requests_for_turn(turn.id)):
            raise ConflictError("A picture for this reply is still being made. Cancel it first, or wait for it.")

    @staticmethod
    def _forget_exchange(uow, chat_id: str, turn) -> list[str]:
        """Delete an exchange, and return the follow-ups still to run because of it.

        Summaries that reach it are dropped too, so nothing goes on remembering
        it; the turn's audit records keep their rows with the turn unset.
        """

        job = uow.session.scalar(select(AsyncJob).where(AsyncJob.turn_id == turn.id))
        try:
            result = json.loads(job.result_json or "{}") if job else {}
        except ValueError:
            result = {}
        followups = [*(result.get("followup_job_ids") or []), result.get("memory_extraction_job_id")]
        said = uow.repo.message(turn.user_message_id)
        if said:
            uow.repo.drop_summaries_from(chat_id, said.created_at)
        uow.repo.delete_messages([turn.assistant_message_id, turn.user_message_id])
        return [value for value in followups if isinstance(value, str) and value]

    def _cancel_followups(self, user_id: str, job_ids: list[str]) -> None:
        # A title, a picture plan or memories for an exchange that is gone
        # would only fail later, or attach themselves to nothing.
        for job_id in job_ids:
            self.jobs.cancel(user_id, job_id)

    def _start(self, ctx, user_id: str, chat_id: str) -> None:
        self.broker.publish(
            ctx.turn_id,
            "turn.queued",
            {"turn_id": ctx.turn_id, "job_id": ctx.job_id, "status": "queued"},
        )
        self.jobs.submit(
            job_id=ctx.job_id,
            job_type="chat",
            user_id=user_id,
            chat_id=chat_id,
            turn_id=ctx.turn_id,
            latency_class="interactive",
            model_key=f"chat:{ctx.model}",
            execution=TurnPipeline(self, ctx).execution(),
        )

    def _open_turn(self, repo, user_id: str, chat_id: str, text: str, values: dict, photos=()):
        """Resolve the turn against its chat and write it, in one transaction."""

        chat = repo.chat(user_id, chat_id)
        if not chat:
            raise NotFoundError("chat not found")
        # ADR 0032: the chat owns its binding. A payload may still repeat the
        # bound values for compatibility, but may never change them, and is
        # refused before anything durable is written.
        _reject_rebinding(values, chat)
        persona = repo.persona(user_id, chat.persona_id) if chat.persona_id else None
        if chat.persona_id and not persona:
            raise NotFoundError("persona not found")
        settings = repo.settings(user_id) or {
            "global_default_model": None,
            "default_memory_mode": "saved",
            "preferences": {},
        }
        preferences = settings.get("preferences") or {}
        model = (
            values.get("model")
            or chat.model_override
            or (persona.default_model if persona else None)
            or settings.get("global_default_model")
        )
        if not model:
            # Asking Ollama what it has is a network call inside this write,
            # so it is made only when nothing else has named a model.
            available_models = self.providers.models()
            model = available_models[0] if available_models else "llama3"
        memory_mode = self._memory_mode(values.get("memory_mode") or chat.memory_mode or "saved")
        if photos and not self.providers.can_see(PERSONA_PROVIDER, model):
            # Refused before anything is written: sending a photo to a model
            # that cannot see it would get a reply about a photo it never saw.
            raise ConflictError(
                f"{model} can't see photos. Choose a model that can, such as Gemma 3, for this persona."
            )

        stamp = now_ts()
        user_message = repo.add_message(chat_id, "user", text, created_at=stamp)
        for photo in photos:
            repo.add_message_photo(
                user_id=user_id,
                message_id=user_message.id,
                content=photo.content,
                width=photo.width,
                height=photo.height,
                mime_type=PHOTO_MIME_TYPE,
            )
        should_generate_title = chat_title_needs_autogeneration(chat.title)
        deterministic_title = None
        if should_generate_title:
            deterministic_title = generate_chat_title_from_first_user_message(text)
            chat.title = deterministic_title
        chat.updated_at = stamp
        chat.memory_mode = memory_mode
        chat.model_override = values.get("model") or chat.model_override
        turn = repo.add_turn(
            user_id=user_id,
            chat_id=chat_id,
            message_id=user_message.id,
            provider=PERSONA_PROVIDER,
            model=model,
        )
        job = repo.add_job(user_id=user_id, chat_id=chat_id, turn_id=turn.id, kind="chat", progress="Queued")

        allow_persona_image_sends = bool(persona.allow_image_sends) if persona else True
        ctx = TurnContext(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            provider_name=PERSONA_PROVIDER,
            model=model,
            memory_mode=memory_mode,
            workspace_id=chat.workspace_id,
            persona_id=chat.persona_id,
            persona_name=persona.name if persona else "",
            persona_instructions=persona_instruction_block(_persona_mapping(persona)),
            example_dialogue=getattr(persona, "card_example_dialogue", "") if persona else "",
            owner_profile=render_owner_profile(preferences),
            allow_persona_image_sends=allow_persona_image_sends,
            explicit_image_request=bool(allow_persona_image_sends and is_high_confidence_image_action_request(text)),
            turn_id=turn.id,
            job_id=job.id,
            user_message_id=user_message.id,
            should_generate_title=should_generate_title,
            deterministic_title=deterministic_title,
            preferences=preferences,
            model_settings=dict(values.get("model_settings") or {}),
            lore_entries=(
                [entry_from_row(row) for row in repo.persona_lore_entries(user_id, persona.id, enabled_only=True)]
                if persona
                else []
            ),
        )
        job_payload = {
            "id": job.id,
            "kind": job.kind,
            "status": job.status,
            "chat_id": job.chat_id,
            "turn_id": job.turn_id,
            "progress": job.progress,
        }
        return ctx, turn_response(turn, job.id), job_payload

    def get_turn(self, user_id: str, turn_id: str) -> dict | None:
        with self._uow() as uow:
            turn = uow.repo.turn(user_id, turn_id)
            if not turn:
                return None
            job = uow.session.scalar(select(AsyncJob).where(AsyncJob.turn_id == turn_id))
            accumulated_text, event_cursor = self.broker.snapshot_state(turn_id)
            return turn_response(turn, job.id if job else None, accumulated_text, event_cursor)

    def context_detail(self, user_id: str, chat_id: str) -> dict | None:
        return self.context.chat_context(user_id, chat_id)

    @staticmethod
    def _memory_mode(value: str | None) -> str:
        return "off" if str(value or "").strip().lower() == "off" else "saved"

    def create_chat_for_turn(self, user_id: str, values: dict) -> str:
        text = str(values.get("text") or "").strip()
        if not text:
            raise RequestError("text required", 400)
        data = dict(values)
        data.setdefault("title", generate_chat_title(text))
        return self.create_chat(user_id, data)["id"]
