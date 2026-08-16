from __future__ import annotations

from sqlalchemy import select

from app.chat import (
    chat_title_needs_autogeneration,
    generate_chat_title,
    generate_chat_title_from_first_user_message,
    parse_model_options,
    persona_instruction_block,
)
from app.capability_service import attachment_response
from app.job_service import JobExecution, JobService, turn_response
from app.context_service import ContextService
from app.memory_service import MemoryService
from app.persona_card import CARD_FIELDS
from app.owner_profile import render_owner_profile
from app.persona_lore import entry_from_row
from app.models import AsyncJob
from app.persona_output import (
    PERSONA_OUTPUT_REMOVED_FALLBACK,
    PersonaOutputStreamFilter,
    safe_persona_output_text,
)
from app.provider_contracts import ChatRequest, ProviderError
from app.provider_registry import ProviderRegistry
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.media_scene import EMPTY_SCENE
from app.task_contracts import (
    CAPABILITY_PLANNING,
    TITLE_GENERATION,
    CapabilityPlanningTaskInput,
    PlannedCapability,
    TitleTaskInput,
    guard_premature_media_completion_claim,
    is_explicit_text_only_request,
    is_high_confidence_image_action_request,
    is_high_confidence_media_action_request,
)
from app.turn_events import TurnEventBroker


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
    ):
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
            attachments = {}
            for row in uow.repo.chat_attachments(user_id, chat_id):
                attachments.setdefault(row.assistant_message_id, []).append(attachment_response(row))
            # A reply produced with reduced context stays explainable after a reload, so the
            # reason travels with the message it produced rather than only with the live turn.
            degraded = {
                turn.assistant_message_id: turn.context_degraded_reason
                for turn in uow.repo.turns_for_chat(user_id, chat_id)
                if turn.assistant_message_id and turn.context_degraded_reason
            }
            messages = [
                {
                    "id": row.id,
                    "role": row.role,
                    "text": safe_persona_output_text(row.text) if row.role == "assistant" else row.text,
                    "created_at": row.created_at,
                    "attachments": attachments.get(row.id, []),
                    "degraded_reason": degraded.get(row.id),
                }
                for row in uow.repo.messages(chat_id)
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

    def create_turn(self, user_id: str, chat_id: str, values: dict) -> tuple[dict, dict]:  # noqa: C901
        text = str(values.get("text") or "").strip()
        if not text:
            raise RequestError("text required", 400)
        provider_name = "ollama"
        with self._uow() as uow:
            repo = uow.repo
            chat = repo.chat(user_id, chat_id)
            if not chat:
                raise NotFoundError("chat not found")
            # ADR 0032: the chat owns its binding. A payload may still repeat
            # the bound values for compatibility, but may never change them,
            # and is refused before anything durable is written.
            _reject_rebinding(values, chat)
            requested_persona_id = chat.persona_id
            persona = repo.persona(user_id, requested_persona_id) if requested_persona_id else None
            if requested_persona_id and not persona:
                raise NotFoundError("persona not found")
            allow_persona_image_sends = bool(persona.allow_image_sends) if persona else True
            workspace_id = chat.workspace_id
            settings = repo.settings(user_id) or {
                "global_default_model": None,
                "default_memory_mode": "saved",
                "preferences": {},
            }
            preferences = settings.get("preferences") or {}
            available_models = self.providers.models()
            model = (
                values.get("model")
                or chat.model_override
                or (persona.default_model if persona else None)
                or settings.get("global_default_model")
                or (available_models[0] if available_models else "llama3")
            )
            memory_mode = self._memory_mode(values.get("memory_mode") or chat.memory_mode or "saved")
            persona_instructions = persona_instruction_block(_persona_mapping(persona))
            lore_entries = (
                [entry_from_row(row) for row in repo.persona_lore_entries(user_id, persona.id, enabled_only=True)]
                if persona
                else []
            )
            stamp = now_ts()
            user_message = repo.add_message(chat_id, "user", text, created_at=stamp)
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
                provider=provider_name,
                model=model,
            )
            job = repo.add_job(
                user_id=user_id,
                chat_id=chat_id,
                turn_id=turn.id,
                kind="chat",
                progress="Queued",
            )
            turn_payload = turn_response(turn, job.id)
            job_payload = {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "chat_id": job.chat_id,
                "turn_id": job.turn_id,
                "progress": job.progress,
            }

        self.broker.publish(turn.id, "turn.queued", {"turn_id": turn.id, "job_id": job.id, "status": "queued"})
        explicit_image_request = bool(allow_persona_image_sends and is_high_confidence_image_action_request(text))

        def deterministic_image_plan() -> PlannedCapability:
            # The user asked in their own words, so those words are the
            # subject. Nothing is invented to fill the other scene fields.
            return PlannedCapability(
                capability_key="media.generate_image",
                prompt=text[:1000],
                operation="generate",
                scene={**EMPTY_SCENE, "subject": text[:200]},
            )

        def execute(token):
            provider = self.providers.chat(provider_name)
            # Edits are included here so the media-claim guard and the planning
            # follow-up are both scheduled when editing is the only capability.
            planning_definitions = self.capabilities.planning_definitions(
                user_id,
                allow_images=allow_persona_image_sends,
                allow_edits=True,
            )
            application_instructions = []
            if planning_definitions:
                application_instructions.append(
                    "A separate platform coordinator handles optional media capabilities. Respond naturally, "
                    "but do not claim an image was sent, taken, attached, matched, or verified. Only the "
                    "platform may make those claims after a durable result exists. Do not choose providers, "
                    "models, workflows, or LoRAs."
                )
            if not allow_persona_image_sends:
                application_instructions.append(
                    "Picture sending is disabled for this persona. Do not promise to make or send a picture. "
                    "If the user asks for one conversationally, briefly explain that they can enable picture "
                    "sending in this persona's settings."
                )
            model_options = parse_model_options(values.get("model_settings") or {})
            plan = self.context.plan(
                turn_id=turn.id,
                user_id=user_id,
                chat_id=chat_id,
                current_message_id=user_message.id,
                workspace_id=workspace_id,
                persona_id=requested_persona_id,
                persona_instructions=persona_instructions,
                persona_name=persona.name if persona else "",
                example_dialogue=getattr(persona, "card_example_dialogue", "") if persona else "",
                lore_entries=lore_entries,
                owner_profile=render_owner_profile(preferences),
                memory_mode=memory_mode,
                preferences=preferences,
                application_instructions=application_instructions,
                provider=provider,
                model=model,
                model_settings=model_options,
                cancellation=token,
            )
            chunks = []
            guard_media_claims = is_high_confidence_media_action_request(text)
            output_filter = PersonaOutputStreamFilter()
            actual_prompt_tokens = None
            request = ChatRequest(
                model=model,
                messages=plan.messages,
                options=plan.options,
                timeout_seconds=self.generation_timeout_seconds,
            )
            for delta in provider.stream(request, token):
                if delta.tool_calls:
                    raise ProviderError(
                        provider=provider_name,
                        code="persona_tool_call_disallowed",
                        user_message="Persona models are not permitted to execute platform capabilities.",
                    )
                if delta.metadata.get("prompt_eval_count") is not None:
                    actual_prompt_tokens = delta.metadata.get("prompt_eval_count")
                if delta.text:
                    sanitized = output_filter.feed(delta.text)
                    if sanitized.text:
                        chunks.append(sanitized.text)
                    if not guard_media_claims and sanitized.text:
                        self.broker.publish(
                            turn.id,
                            "assistant.delta",
                            {"turn_id": turn.id, "text": sanitized.text},
                        )
            sanitized_tail = output_filter.finish()
            if sanitized_tail.text:
                chunks.append(sanitized_tail.text)
                if not guard_media_claims:
                    self.broker.publish(
                        turn.id,
                        "assistant.delta",
                        {"turn_id": turn.id, "text": sanitized_tail.text},
                    )
            raw_reply = "".join(chunks)
            if output_filter.protected_content_removed and not raw_reply.strip():
                raw_reply = PERSONA_OUTPUT_REMOVED_FALLBACK
                if not guard_media_claims:
                    self.broker.publish(
                        turn.id,
                        "assistant.delta",
                        {"turn_id": turn.id, "text": raw_reply},
                    )
            reply, media_claim_guarded = guard_premature_media_completion_claim(
                text,
                raw_reply,
                image_sends_allowed=allow_persona_image_sends,
            )
            if guard_media_claims and reply:
                self.broker.publish(
                    turn.id,
                    "assistant.delta",
                    {"turn_id": turn.id, "text": reply},
                )
            self.broker.replace_accumulated_text(turn.id, reply)
            self.context.record_actual_prompt_tokens(turn.id, actual_prompt_tokens)
            return {
                "text": reply,
                "chatId": chat_id,
                "mediaClaimGuarded": media_claim_guarded,
                "schedule_capability_planning": bool(
                    not is_explicit_text_only_request(text) and (planning_definitions or explicit_image_request)
                ),
            }

        def execute_title_followup(token):
            try:
                outcome = self.task_models.run(
                    user_id,
                    TITLE_GENERATION,
                    TitleTaskInput(text),
                    token,
                    chat_id=chat_id,
                    turn_id=turn.id,
                )
                return {"task_title": outcome.output.title, "task_run_id": outcome.run_id}
            except ProviderError as exc:
                if exc.code == "cancelled" or token.cancelled:
                    raise
                return {"task_title": None, "task_run_id": None}

        def on_title_followup_success(repo, result):
            output = dict(result or {})
            task_title = output.get("task_title")
            durable_chat = repo.chat(user_id, chat_id)
            if should_generate_title and task_title and durable_chat and durable_chat.title == deterministic_title:
                durable_chat.title = task_title
                durable_chat.updated_at = now_ts()
            return output

        def execute_capability_followup(token):
            # Editing is offered only when this conversation still holds an
            # image the platform can resolve for the user.
            offered_attachments = self.capabilities.planning_attachments(user_id, chat_id)
            allow_edits = bool(offered_attachments.available)
            planning_definitions = self.capabilities.planning_definitions(
                user_id,
                allow_images=allow_persona_image_sends,
                allow_edits=allow_edits,
            )
            if is_explicit_text_only_request(text):
                return {"planned_capabilities": [], "task_run_id": None}
            if not planning_definitions:
                return {
                    "planned_capabilities": [deterministic_image_plan()] if explicit_image_request else [],
                    "task_run_id": None,
                    "planning_source": "deterministic_explicit_image",
                }
            planning_vocabulary = self.capabilities.planning_vocabulary(user_id, allow_edits=allow_edits)
            planning_context = self.capabilities.planning_context(user_id, chat_id)
            offered_presets = self.capabilities.planning_presets(user_id)
            try:
                outcome = self.task_models.run(
                    user_id,
                    CAPABILITY_PLANNING,
                    CapabilityPlanningTaskInput(
                        user_text=text,
                        available_capabilities=planning_definitions,
                        persona_selected=bool(requested_persona_id),
                        available_operations=tuple(planning_vocabulary.get("operations") or ("generate",)),
                        available_domains=tuple(planning_vocabulary.get("domains") or ()),
                        available_content_tags=tuple(planning_vocabulary.get("content_tags") or ()),
                        available_features=tuple(planning_vocabulary.get("features") or ()),
                        available_attachments=offered_attachments.available,
                        available_presets=offered_presets.available,
                        recent_user_messages=planning_context,
                    ),
                    token,
                    chat_id=chat_id,
                    turn_id=turn.id,
                )
                planned_capabilities = list(outcome.output.requests)
                planning_source = "task_model"
                if explicit_image_request and not any(
                    item.capability_key == "media.generate_image" for item in planned_capabilities
                ):
                    planned_capabilities.append(deterministic_image_plan())
                    planning_source = "task_model_with_explicit_image_fallback"
                return {
                    "planned_capabilities": planned_capabilities,
                    "task_run_id": outcome.run_id,
                    "planning_source": planning_source,
                    # Carried, not persisted, so a reference resolves to the
                    # image this plan was actually offered.
                    "offered_attachments": offered_attachments.bindings,
                    "offered_presets": offered_presets.bindings,
                    "planning_context": planning_context,
                }
            except ProviderError as exc:
                if exc.code == "cancelled" or token.cancelled:
                    raise
                return {
                    "planned_capabilities": [deterministic_image_plan()] if explicit_image_request else [],
                    "task_run_id": None,
                    "planning_source": "deterministic_explicit_image",
                }

        def on_capability_followup_success(repo, result):
            output = dict(result or {})
            planned_capabilities = list(output.pop("planned_capabilities", []))
            planning_source = str(output.pop("planning_source", "task_model"))
            offered = output.pop("offered_attachments", None)
            offered_presets = output.pop("offered_presets", None)
            planning_context = tuple(output.pop("planning_context", ()) or ())
            if planned_capabilities:
                capability_requests = self.capabilities.prepare_planned_requests(
                    repo,
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=turn.id,
                    user_text=text,
                    originating_persona_id=requested_persona_id,
                    planned=planned_capabilities,
                    source=planning_source,
                    offered_attachments=offered,
                    offered_presets=offered_presets,
                    planning_context=planning_context,
                )
                output["auto_capability_request_ids"] = [
                    item["id"] for item in capability_requests if item.pop("auto_submit", False)
                ]
                output["capability_requests"] = capability_requests
            return output

        def after_capability_followup_success(result):
            for request_id in (result or {}).get("auto_capability_request_ids") or []:
                try:
                    self.capabilities.submit_queued(user_id, request_id)
                except Exception as exc:  # noqa: BLE001 - durable attachment exposes a retryable failure
                    self.capabilities.fail_queued_submission(user_id, request_id)
                    self.capabilities.logger.error(
                        "automatic capability submission failed request_id=%s error=%s",
                        request_id,
                        exc.__class__.__name__,
                    )

        def on_success(repo, result):
            reply = str((result or {}).get("text") or "")
            assistant = repo.add_message(chat_id, "assistant", reply)
            durable_turn = repo.turn_by_id(turn.id)
            durable_turn.assistant_message_id = assistant.id
            durable_chat = repo.chat(user_id, chat_id)
            durable_chat.updated_at = now_ts()
            output = dict(result or {})
            output.update({"text": reply, "chatId": chat_id})
            should_plan = bool(output.pop("schedule_capability_planning", False))
            background_job_ids = []
            if should_generate_title:
                output["title_job_id"] = repo.add_job(
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=None,
                    kind="title_followup",
                    progress="Queued for title follow-up",
                ).id
                background_job_ids.append(output["title_job_id"])
            if should_plan:
                output["capability_planning_job_id"] = repo.add_job(
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=None,
                    kind="capability_followup",
                    progress="Queued for capability planning",
                ).id
                background_job_ids.append(output["capability_planning_job_id"])
            if background_job_ids:
                output["followup_job_ids"] = background_job_ids
                output["followup_job_id"] = output.get("capability_planning_job_id") or output.get("title_job_id")
            if memory_mode == "saved":
                output["memory_extraction_job_id"] = self.memory.prepare_extraction_job(
                    repo,
                    user_id=user_id,
                    chat_id=chat_id,
                )
            return output

        def after_success(result):
            title_job_id = (result or {}).get("title_job_id")
            if title_job_id:
                try:
                    self.jobs.submit(
                        job_id=title_job_id,
                        job_type="task_model",
                        user_id=user_id,
                        chat_id=chat_id,
                        turn_id=None,
                        latency_class="standard",
                        model_key="task:title_generation",
                        ordering_key=f"chat:{chat_id}",
                        execution=JobExecution(
                            execute=execute_title_followup,
                            on_success=on_title_followup_success,
                        ),
                    )
                except Exception:
                    self.jobs.fail_unsubmitted(
                        title_job_id,
                        "Title follow-up could not start.",
                    )
            capability_job_id = (result or {}).get("capability_planning_job_id")
            if capability_job_id:
                try:
                    self.jobs.submit(
                        job_id=capability_job_id,
                        job_type="task_model",
                        user_id=user_id,
                        chat_id=chat_id,
                        turn_id=None,
                        latency_class="standard",
                        model_key="task:capability_planning",
                        ordering_key=f"chat:{chat_id}",
                        execution=JobExecution(
                            execute=execute_capability_followup,
                            on_success=on_capability_followup_success,
                            after_success=after_capability_followup_success,
                        ),
                    )
                except Exception:
                    self.jobs.fail_unsubmitted(
                        capability_job_id,
                        "Capability planning could not start.",
                    )
            extraction_job_id = (result or {}).get("memory_extraction_job_id")
            if extraction_job_id:
                self.memory.submit_extraction(
                    job_id=extraction_job_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=turn.id,
                    message_id=user_message.id,
                    user_text=text,
                    workspace_id=workspace_id,
                    persona_id=requested_persona_id,
                )

        self.jobs.submit(
            job_id=job.id,
            job_type="chat",
            user_id=user_id,
            chat_id=chat_id,
            turn_id=turn.id,
            latency_class="interactive",
            model_key=f"chat:{model}",
            execution=JobExecution(execute=execute, on_success=on_success, after_success=after_success),
        )
        return turn_payload, job_payload

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
