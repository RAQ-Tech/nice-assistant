"""What happens to a turn after its row exists.

`create_turn` used to hold all of this as closures over its own local
variables, which made one function responsible for resolving the turn,
persisting it, streaming the reply, and scheduling every follow-up. Voice work
has to touch streaming and interruption, and that was not a safe place to touch.

Nothing here changes behaviour. The closures became methods, and the variables
they closed over became one frozen record created inside the transaction that
wrote the turn. The ordering, the guards, and the failure handling are the same
ones that were here before, moved rather than rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.chat import parse_model_options
from app.job_service import JobExecution
from app.media_scene import EMPTY_SCENE
from app.persona_output import PERSONA_OUTPUT_REMOVED_FALLBACK, PersonaOutputStreamFilter
from app.provider_contracts import ChatRequest, ProviderError
from app.repositories import now_ts
from app.task_contracts import (
    CAPABILITY_PLANNING,
    TITLE_GENERATION,
    CapabilityPlanningTaskInput,
    PlannedCapability,
    TitleTaskInput,
    guard_premature_media_completion_claim,
    is_explicit_text_only_request,
    is_high_confidence_media_action_request,
)


MEDIA_CLAIM_INSTRUCTION = (
    "A separate platform coordinator handles optional media capabilities. Respond naturally, "
    "but do not claim an image was sent, taken, attached, matched, or verified. Only the "
    "platform may make those claims after a durable result exists. Do not choose providers, "
    "models, workflows, or LoRAs."
)
IMAGE_SENDS_DISABLED_INSTRUCTION = (
    "Picture sending is disabled for this persona. Do not promise to make or send a picture. "
    "If the user asks for one conversationally, briefly explain that they can enable picture "
    "sending in this persona's settings."
)


@dataclass(frozen=True)
class TurnContext:
    """Everything the turn resolved before its transaction committed.

    Frozen because every value here was decided once, inside that transaction.
    A follow-up that re-read any of them could see a different answer than the
    reply did, which is the class of bug this record exists to prevent.
    """

    user_id: str
    chat_id: str
    text: str
    provider_name: str
    model: str
    memory_mode: str
    workspace_id: str | None
    persona_id: str | None
    persona_name: str
    persona_instructions: str
    example_dialogue: str
    owner_profile: str
    allow_persona_image_sends: bool
    explicit_image_request: bool
    turn_id: str
    job_id: str
    user_message_id: str
    should_generate_title: bool
    deterministic_title: str | None
    preferences: dict = field(default_factory=dict)
    model_settings: dict = field(default_factory=dict)
    lore_entries: list = field(default_factory=list)


class TurnPipeline:
    """Generation and follow-ups for one turn."""

    def __init__(self, service, ctx: TurnContext):
        self.ctx = ctx
        self.providers = service.providers
        self.context = service.context
        self.capabilities = service.capabilities
        self.task_models = service.task_models
        self.memory = service.memory
        self.jobs = service.jobs
        self.broker = service.broker
        self.generation_timeout_seconds = service.generation_timeout_seconds

    # -- generating the reply ---------------------------------------------

    def execution(self) -> JobExecution:
        return JobExecution(
            execute=self.generate,
            on_success=self.on_generated,
            after_success=self.after_generated,
        )

    def _application_instructions(self, planning_definitions) -> list[str]:
        instructions = []
        if planning_definitions:
            instructions.append(MEDIA_CLAIM_INSTRUCTION)
        if not self.ctx.allow_persona_image_sends:
            instructions.append(IMAGE_SENDS_DISABLED_INSTRUCTION)
        return instructions

    def generate(self, token) -> dict:
        ctx = self.ctx
        provider = self.providers.chat(ctx.provider_name)
        # Edits are included here so the media-claim guard and the planning
        # follow-up are both scheduled when editing is the only capability.
        planning_definitions = self.capabilities.planning_definitions(
            ctx.user_id,
            allow_images=ctx.allow_persona_image_sends,
            allow_edits=True,
        )
        plan = self.context.plan(
            turn_id=ctx.turn_id,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            current_message_id=ctx.user_message_id,
            workspace_id=ctx.workspace_id,
            persona_id=ctx.persona_id,
            persona_instructions=ctx.persona_instructions,
            persona_name=ctx.persona_name,
            example_dialogue=ctx.example_dialogue,
            lore_entries=ctx.lore_entries,
            owner_profile=ctx.owner_profile,
            memory_mode=ctx.memory_mode,
            preferences=ctx.preferences,
            application_instructions=self._application_instructions(planning_definitions),
            provider=provider,
            model=ctx.model,
            model_settings=parse_model_options(ctx.model_settings),
            cancellation=token,
        )
        raw_reply, actual_prompt_tokens, finish_reason = self._stream(provider, plan, token)
        reply, media_claim_guarded = guard_premature_media_completion_claim(
            ctx.text,
            raw_reply,
            image_sends_allowed=ctx.allow_persona_image_sends,
        )
        if is_high_confidence_media_action_request(ctx.text) and reply:
            # Guarded output is published once, after the guard has seen all of
            # it, rather than streamed and then corrected.
            self._publish(reply)
        self.broker.replace_accumulated_text(ctx.turn_id, reply)
        self.context.record_actual_prompt_tokens(ctx.turn_id, actual_prompt_tokens)
        # "length" is what both Ollama and OpenAI say when a reply stopped
        # because it ran out of allowance rather than because it was finished.
        # Without recording it, a severed sentence is indistinguishable from a
        # persona choosing to trail off.
        if str(finish_reason or "").strip().lower() == "length":
            self.context.record_reply_truncated(ctx.turn_id)
        return {
            "text": reply,
            "chatId": ctx.chat_id,
            "mediaClaimGuarded": media_claim_guarded,
            "schedule_capability_planning": bool(
                not is_explicit_text_only_request(ctx.text) and (planning_definitions or ctx.explicit_image_request)
            ),
        }

    def _publish(self, text: str) -> None:
        self.broker.publish(self.ctx.turn_id, "assistant.delta", {"turn_id": self.ctx.turn_id, "text": text})

    def _stream(self, provider, plan, token) -> tuple[str, int | None]:
        """Consume the provider stream, filtered, and return what it said."""

        ctx = self.ctx
        guard_media_claims = is_high_confidence_media_action_request(ctx.text)
        output_filter = PersonaOutputStreamFilter()
        chunks: list[str] = []
        actual_prompt_tokens = None
        finish_reason = None
        request = ChatRequest(
            model=ctx.model,
            messages=plan.messages,
            options=plan.options,
            timeout_seconds=self.generation_timeout_seconds,
        )
        for delta in provider.stream(request, token):
            if delta.tool_calls:
                raise ProviderError(
                    provider=ctx.provider_name,
                    code="persona_tool_call_disallowed",
                    user_message="Persona models are not permitted to execute platform capabilities.",
                )
            if delta.metadata.get("prompt_eval_count") is not None:
                actual_prompt_tokens = delta.metadata.get("prompt_eval_count")
            if delta.finish_reason:
                finish_reason = delta.finish_reason
            if not delta.text:
                continue
            sanitized = output_filter.feed(delta.text)
            if sanitized.text:
                chunks.append(sanitized.text)
                if not guard_media_claims:
                    self._publish(sanitized.text)
        sanitized_tail = output_filter.finish()
        if sanitized_tail.text:
            chunks.append(sanitized_tail.text)
            if not guard_media_claims:
                self._publish(sanitized_tail.text)
        raw_reply = "".join(chunks)
        if output_filter.protected_content_removed and not raw_reply.strip():
            raw_reply = PERSONA_OUTPUT_REMOVED_FALLBACK
            if not guard_media_claims:
                self._publish(raw_reply)
        return raw_reply, actual_prompt_tokens, finish_reason

    # -- committing the reply and scheduling what follows ------------------

    def on_generated(self, repo, result) -> dict:
        ctx = self.ctx
        reply = str((result or {}).get("text") or "")
        assistant = repo.add_message(ctx.chat_id, "assistant", reply)
        durable_turn = repo.turn_by_id(ctx.turn_id)
        durable_turn.assistant_message_id = assistant.id
        durable_chat = repo.chat(ctx.user_id, ctx.chat_id)
        durable_chat.updated_at = now_ts()
        output = dict(result or {})
        output.update({"text": reply, "chatId": ctx.chat_id})
        should_plan = bool(output.pop("schedule_capability_planning", False))
        background_job_ids = []
        if ctx.should_generate_title:
            output["title_job_id"] = self._followup_job(repo, "title_followup", "Queued for title follow-up")
            background_job_ids.append(output["title_job_id"])
        if should_plan:
            output["capability_planning_job_id"] = self._followup_job(
                repo, "capability_followup", "Queued for capability planning"
            )
            background_job_ids.append(output["capability_planning_job_id"])
        if background_job_ids:
            output["followup_job_ids"] = background_job_ids
            output["followup_job_id"] = output.get("capability_planning_job_id") or output.get("title_job_id")
        if ctx.memory_mode == "saved":
            output["memory_extraction_job_id"] = self.memory.prepare_extraction_job(
                repo,
                user_id=ctx.user_id,
                chat_id=ctx.chat_id,
            )
        return output

    def _followup_job(self, repo, kind: str, progress: str) -> str:
        return repo.add_job(
            user_id=self.ctx.user_id,
            chat_id=self.ctx.chat_id,
            turn_id=None,
            kind=kind,
            progress=progress,
        ).id

    def after_generated(self, result) -> None:
        ctx = self.ctx
        values = result or {}
        self._submit_followup(
            values.get("title_job_id"),
            "task:title_generation",
            JobExecution(execute=self.run_title, on_success=self.on_title),
            "Title follow-up could not start.",
        )
        self._submit_followup(
            values.get("capability_planning_job_id"),
            "task:capability_planning",
            JobExecution(
                execute=self.run_planning,
                on_success=self.on_planning,
                after_success=self.after_planning,
            ),
            "Capability planning could not start.",
        )
        extraction_job_id = values.get("memory_extraction_job_id")
        if extraction_job_id:
            self.memory.submit_extraction(
                job_id=extraction_job_id,
                user_id=ctx.user_id,
                chat_id=ctx.chat_id,
                turn_id=ctx.turn_id,
                message_id=ctx.user_message_id,
                user_text=ctx.text,
                workspace_id=ctx.workspace_id,
                persona_id=ctx.persona_id,
            )

    def _submit_followup(self, job_id, model_key: str, execution: JobExecution, failure_message: str) -> None:
        if not job_id:
            return
        try:
            self.jobs.submit(
                job_id=job_id,
                job_type="task_model",
                user_id=self.ctx.user_id,
                chat_id=self.ctx.chat_id,
                turn_id=None,
                latency_class="standard",
                model_key=model_key,
                ordering_key=f"chat:{self.ctx.chat_id}",
                execution=execution,
            )
        except Exception:
            self.jobs.fail_unsubmitted(job_id, failure_message)

    # -- title -------------------------------------------------------------

    def run_title(self, token) -> dict:
        ctx = self.ctx
        try:
            outcome = self.task_models.run(
                ctx.user_id,
                TITLE_GENERATION,
                TitleTaskInput(ctx.text),
                token,
                chat_id=ctx.chat_id,
                turn_id=ctx.turn_id,
            )
            return {"task_title": outcome.output.title, "task_run_id": outcome.run_id}
        except ProviderError as exc:
            if exc.code == "cancelled" or token.cancelled:
                raise
            return {"task_title": None, "task_run_id": None}

    def on_title(self, repo, result) -> dict:
        ctx = self.ctx
        output = dict(result or {})
        task_title = output.get("task_title")
        durable_chat = repo.chat(ctx.user_id, ctx.chat_id)
        # Only replaces the deterministic title this turn wrote. Anything else
        # means somebody renamed the chat meanwhile, and they win.
        if ctx.should_generate_title and task_title and durable_chat and durable_chat.title == ctx.deterministic_title:
            durable_chat.title = task_title
            durable_chat.updated_at = now_ts()
        return output

    # -- capability planning ----------------------------------------------

    def _deterministic_image_plan(self) -> PlannedCapability:
        # The user asked in their own words, so those words are the subject.
        # Nothing is invented to fill the other scene fields.
        return PlannedCapability(
            capability_key="media.generate_image",
            prompt=self.ctx.text[:1000],
            operation="generate",
            scene={**EMPTY_SCENE, "subject": self.ctx.text[:200]},
        )

    def _explicit_image_only(self) -> dict:
        return {
            "planned_capabilities": [self._deterministic_image_plan()] if self.ctx.explicit_image_request else [],
            "task_run_id": None,
            "planning_source": "deterministic_explicit_image",
        }

    def run_planning(self, token) -> dict:
        ctx = self.ctx
        # Editing is offered only when this conversation still holds an image
        # the platform can resolve for the user.
        offered_attachments = self.capabilities.planning_attachments(ctx.user_id, ctx.chat_id)
        allow_edits = bool(offered_attachments.available)
        planning_definitions = self.capabilities.planning_definitions(
            ctx.user_id,
            allow_images=ctx.allow_persona_image_sends,
            allow_edits=allow_edits,
        )
        if is_explicit_text_only_request(ctx.text):
            return {"planned_capabilities": [], "task_run_id": None}
        if not planning_definitions:
            return self._explicit_image_only()
        planning_vocabulary = self.capabilities.planning_vocabulary(ctx.user_id, allow_edits=allow_edits)
        planning_context = self.capabilities.planning_context(ctx.user_id, ctx.chat_id)
        offered_presets = self.capabilities.planning_presets(ctx.user_id)
        try:
            outcome = self.task_models.run(
                ctx.user_id,
                CAPABILITY_PLANNING,
                CapabilityPlanningTaskInput(
                    user_text=ctx.text,
                    available_capabilities=planning_definitions,
                    persona_selected=bool(ctx.persona_id),
                    available_operations=tuple(planning_vocabulary.get("operations") or ("generate",)),
                    available_domains=tuple(planning_vocabulary.get("domains") or ()),
                    available_content_tags=tuple(planning_vocabulary.get("content_tags") or ()),
                    available_features=tuple(planning_vocabulary.get("features") or ()),
                    available_attachments=offered_attachments.available,
                    available_presets=offered_presets.available,
                    recent_user_messages=planning_context,
                ),
                token,
                chat_id=ctx.chat_id,
                turn_id=ctx.turn_id,
            )
        except ProviderError as exc:
            if exc.code == "cancelled" or token.cancelled:
                raise
            return self._explicit_image_only()
        planned_capabilities = list(outcome.output.requests)
        planning_source = "task_model"
        if ctx.explicit_image_request and not any(
            item.capability_key == "media.generate_image" for item in planned_capabilities
        ):
            planned_capabilities.append(self._deterministic_image_plan())
            planning_source = "task_model_with_explicit_image_fallback"
        return {
            "planned_capabilities": planned_capabilities,
            "task_run_id": outcome.run_id,
            "planning_source": planning_source,
            # Carried, not persisted, so a reference resolves to the image this
            # plan was actually offered.
            "offered_attachments": offered_attachments.bindings,
            "offered_presets": offered_presets.bindings,
            "planning_context": planning_context,
        }

    def on_planning(self, repo, result) -> dict:
        ctx = self.ctx
        output = dict(result or {})
        planned_capabilities = list(output.pop("planned_capabilities", []))
        planning_source = str(output.pop("planning_source", "task_model"))
        offered = output.pop("offered_attachments", None)
        offered_presets = output.pop("offered_presets", None)
        planning_context = tuple(output.pop("planning_context", ()) or ())
        if not planned_capabilities:
            return output
        capability_requests = self.capabilities.prepare_planned_requests(
            repo,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            turn_id=ctx.turn_id,
            user_text=ctx.text,
            originating_persona_id=ctx.persona_id,
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

    def after_planning(self, result) -> None:
        for request_id in (result or {}).get("auto_capability_request_ids") or []:
            try:
                self.capabilities.submit_queued(self.ctx.user_id, request_id)
            except Exception as exc:  # noqa: BLE001 - durable attachment exposes a retryable failure
                self.capabilities.fail_queued_submission(self.ctx.user_id, request_id)
                self.capabilities.logger.error(
                    "automatic capability submission failed request_id=%s error=%s",
                    request_id,
                    exc.__class__.__name__,
                )
