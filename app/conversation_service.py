from __future__ import annotations

from sqlalchemy import select

from app.chat import (
    chat_title_needs_autogeneration,
    generate_chat_title,
    generate_chat_title_from_first_user_message,
    parse_model_options,
    persona_instruction_block,
)
from app.job_service import JobExecution, JobService, turn_response
from app.context_service import ContextService
from app.memory_service import MemoryService
from app.models import AsyncJob
from app.provider_contracts import ChatRequest, ProviderError
from app.provider_registry import ProviderRegistry
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError
from app.task_contracts import (
    CAPABILITY_PLANNING,
    TITLE_GENERATION,
    CapabilityPlanningTaskInput,
    TitleTaskInput,
    is_explicit_text_only_request,
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
            messages = [
                {"id": row.id, "role": row.role, "text": row.text, "created_at": row.created_at}
                for row in uow.repo.messages(chat_id)
            ]
            return {"chat": _chat_response(chat), "messages": messages}

    def update_chat(self, user_id: str, chat_id: str, values: dict) -> dict | None:
        with self._uow() as uow:
            chat = uow.repo.chat(user_id, chat_id)
            if not chat:
                return None
            if "persona_id" in values and values["persona_id"]:
                persona = uow.repo.persona(user_id, values["persona_id"])
                if not persona:
                    raise NotFoundError("persona not found")
                chat.persona_id = persona.id
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

    def create_turn(self, user_id: str, chat_id: str, values: dict) -> tuple[dict, dict]:
        text = str(values.get("text") or "").strip()
        if not text:
            raise RequestError("text required", 400)
        provider_name = "ollama"
        with self._uow() as uow:
            repo = uow.repo
            chat = repo.chat(user_id, chat_id)
            if not chat:
                raise NotFoundError("chat not found")
            requested_persona_id = values.get("persona_id") or chat.persona_id
            persona = repo.persona(user_id, requested_persona_id) if requested_persona_id else None
            if requested_persona_id and not persona:
                raise NotFoundError("persona not found")
            workspace_id = (
                values.get("workspace_id") or chat.workspace_id or (persona.workspace_id if persona else None)
            )
            if workspace_id and not repo.workspace(user_id, workspace_id):
                raise NotFoundError("workspace not found")
            if persona and workspace_id not in repo.persona_workspace_ids(persona.id):
                raise NotFoundError("persona not found")
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
            stamp = now_ts()
            user_message = repo.add_message(chat_id, "user", text, created_at=stamp)
            should_generate_title = chat_title_needs_autogeneration(chat.title)
            if should_generate_title:
                chat.title = generate_chat_title_from_first_user_message(text)
            chat.updated_at = stamp
            chat.memory_mode = memory_mode
            chat.persona_id = requested_persona_id
            chat.workspace_id = workspace_id
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

        def execute(token):
            provider = self.providers.chat(provider_name)
            planning_definitions = self.capabilities.planning_definitions(user_id)
            planning_vocabulary = self.capabilities.planning_vocabulary(user_id) if planning_definitions else {}
            application_instructions = (
                [
                    (
                        "A separate platform coordinator handles optional media capabilities. Respond naturally, "
                        "but do not claim that media has already been generated or choose providers, models, "
                        "workflows, or LoRAs."
                    )
                ]
                if planning_definitions
                else []
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
                memory_mode=memory_mode,
                preferences=preferences,
                application_instructions=application_instructions,
                provider=provider,
                model=model,
                model_settings=model_options,
                cancellation=token,
            )
            chunks = []
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
                    chunks.append(delta.text)
                    self.broker.publish(
                        turn.id,
                        "assistant.delta",
                        {"turn_id": turn.id, "text": delta.text},
                    )
            reply = "".join(chunks)
            self.broker.replace_accumulated_text(turn.id, reply)
            self.context.record_actual_prompt_tokens(turn.id, actual_prompt_tokens)
            task_run_ids = {}
            task_title = None
            planned_capabilities = []
            if should_generate_title:
                try:
                    outcome = self.task_models.run(
                        user_id,
                        TITLE_GENERATION,
                        TitleTaskInput(text),
                        token,
                        chat_id=chat_id,
                        turn_id=turn.id,
                    )
                    task_title = outcome.output.title
                    task_run_ids[TITLE_GENERATION] = outcome.run_id
                except ProviderError as exc:
                    if exc.code == "cancelled" or token.cancelled:
                        raise
            if planning_definitions and not is_explicit_text_only_request(text):
                try:
                    outcome = self.task_models.run(
                        user_id,
                        CAPABILITY_PLANNING,
                        CapabilityPlanningTaskInput(
                            user_text=text,
                            assistant_text=reply,
                            available_capabilities=planning_definitions,
                            persona_selected=bool(requested_persona_id),
                            available_operations=tuple(planning_vocabulary.get("operations") or ("generate",)),
                            available_domains=tuple(planning_vocabulary.get("domains") or ()),
                            available_content_tags=tuple(planning_vocabulary.get("content_tags") or ()),
                            available_features=tuple(planning_vocabulary.get("features") or ()),
                        ),
                        token,
                        chat_id=chat_id,
                        turn_id=turn.id,
                    )
                    planned_capabilities = list(outcome.output.requests)
                    task_run_ids[CAPABILITY_PLANNING] = outcome.run_id
                except ProviderError as exc:
                    if exc.code == "cancelled" or token.cancelled:
                        raise
            return {
                "text": reply,
                "chatId": chat_id,
                "task_title": task_title,
                "planned_capabilities": planned_capabilities,
                "task_run_ids": task_run_ids,
            }

        def on_success(repo, result):
            reply = str((result or {}).get("text") or "")
            assistant = repo.add_message(chat_id, "assistant", reply)
            durable_turn = repo.turn_by_id(turn.id)
            durable_turn.assistant_message_id = assistant.id
            durable_chat = repo.chat(user_id, chat_id)
            durable_chat.updated_at = now_ts()
            output = dict(result or {})
            output.update({"text": reply, "chatId": chat_id})
            task_title = output.pop("task_title", None)
            if should_generate_title and task_title:
                durable_chat.title = task_title
            planned_capabilities = list(output.pop("planned_capabilities", []))
            if planned_capabilities:
                output["capability_requests"] = self.capabilities.prepare_planned_requests(
                    repo,
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=turn.id,
                    planned=planned_capabilities,
                )
            if memory_mode == "saved":
                output["memory_extraction_job_id"] = self.memory.prepare_extraction_job(
                    repo,
                    user_id=user_id,
                    chat_id=chat_id,
                )
            return output

        def after_success(result):
            extraction_job_id = (result or {}).get("memory_extraction_job_id")
            if not extraction_job_id:
                return
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
            return turn_response(
                turn,
                job.id if job else None,
                self.broker.accumulated_text(turn_id),
            )

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
