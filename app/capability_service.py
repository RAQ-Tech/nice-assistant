from __future__ import annotations

import json
import secrets

from app.auth import redact_sensitive_text
from app.capability_contracts import (
    CAPABILITY_LEGAL_TRANSITIONS,
    CAPABILITY_TERMINAL_STATES,
    CapabilityRegistry,
)
from app.job_service import JobExecution, JobService
from app.identity_conditioning import IDENTITY_CONTROL_FEATURE
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.task_contracts import AvailableCapability, PlannedCapability


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class InvalidCapabilityTransition(RuntimeError):
    pass


def transition_capability(
    repo,
    request,
    state: str,
    action: str,
    *,
    code: str | None = None,
    message: str | None = None,
    result: dict | None = None,
) -> None:
    previous = request.status
    if state != previous and state not in CAPABILITY_LEGAL_TRANSITIONS.get(previous, set()):
        raise InvalidCapabilityTransition(f"invalid capability transition: {previous} -> {state}")
    stamp = now_ts()
    request.status = state
    if action in {"approved", "denied"}:
        request.decided_at = stamp
    if state == "running":
        request.started_at = stamp
    if state in CAPABILITY_TERMINAL_STATES:
        request.completed_at = stamp
    request.error_code = code
    request.error_message = redact_sensitive_text(message or "")[:1000] or None
    if result is not None:
        request.result_json = json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)
    repo.add_capability_event(
        request,
        action,
        from_status=previous,
        to_status=state,
        detail={"code": code} if code else {},
    )


class CapabilityService:
    def __init__(
        self,
        session_factory,
        secret_store,
        registry: CapabilityRegistry,
        jobs: JobService,
        media,
        media_catalog,
        logger,
        provider_url_policy=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.registry = registry
        self.jobs = jobs
        self.media = media
        self.media_catalog = media_catalog
        self.logger = logger
        self.provider_url_policy = provider_url_policy

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def _enabled_keys(self, user_id: str) -> set[str]:
        enabled = set()
        if self.media_catalog.has_ready_resource(user_id, "image"):
            enabled.add("media.generate_image")
        if self.media_catalog.has_ready_resource(user_id, "video"):
            enabled.add("media.generate_video")
        if any(
            self.media_catalog.has_ready_operation(user_id, "image", operation)
            for operation in ("image_to_image", "inpaint", "outpaint")
        ):
            enabled.add("media.edit_image")
        return enabled

    def planning_definitions(self, user_id: str) -> tuple[AvailableCapability, ...]:
        enabled = self._enabled_keys(user_id)
        return tuple(
            AvailableCapability(item.key, item.title, item.description)
            for item in self.registry.definitions()
            if item.key in enabled and item.key != "media.edit_image"
        )

    def planning_vocabulary(self, user_id: str) -> dict:
        vocabulary = self.media_catalog.vocabulary(user_id)
        if "media.generate_image" in self._enabled_keys(user_id):
            vocabulary["features"] = sorted(set(vocabulary.get("features") or ()) | {IDENTITY_CONTROL_FEATURE})
        vocabulary["operations"] = ["generate"]
        return vocabulary

    def definitions(self, user_id: str) -> list[dict]:
        enabled = self._enabled_keys(user_id)
        return [{**item.public(), "available": item.key in enabled} for item in self.registry.definitions()]

    def list_requests(
        self,
        user_id: str,
        *,
        chat_id: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict]:
        with self._uow() as uow:
            if chat_id and not uow.repo.chat(user_id, chat_id):
                raise NotFoundError("chat not found")
            return [
                self._response(uow.repo, row)
                for row in uow.repo.capability_requests(user_id, chat_id=chat_id, statuses=statuses)
            ]

    def get(self, user_id: str, request_id: str) -> dict | None:
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            return self._response(uow.repo, row) if row else None

    def replan(self, user_id: str, request_id: str) -> dict | None:
        """Refresh a still-pending coordinator plan after operator settings change."""
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            if row.status != "pending_confirmation":
                raise ConflictError(f"capability request is {row.status}")
            current = uow.repo.media_execution_plan_for_capability(user_id, row.id)
            if not current or current.source != "coordinator":
                raise ConflictError("Only a pending coordinated media plan can be refreshed.")
            previous_plan_status = current.status
            arguments = _json_object(row.arguments_json)
            definition = self.registry.by_key(row.capability_key)
            chat = uow.repo.chat(user_id, row.chat_id) if row.chat_id else None
            requirements = {
                "kind": definition.kind,
                "operation": arguments.get("operation") or "generate",
                "domains": arguments.get("domains") or [],
                "content_tags": arguments.get("content_tags") or [],
                "required_features": arguments.get("required_features") or [],
            }
            persona_id = chat.persona_id if chat else None
            adopted_legacy_persona_id = None
            if "identity_control" in requirements["required_features"]:
                if current.persona_id and (not chat or chat.persona_id != current.persona_id):
                    raise ConflictError(
                        "The chat persona changed after this identity request was planned. Create a new request."
                    )
                if current.persona_id:
                    persona_id = current.persona_id
                elif not persona_id:
                    raise ConflictError(
                        "The legacy blocked identity plan has no chat persona to adopt. Create a new request."
                    )
                else:
                    adopted_legacy_persona_id = persona_id
            plan = self.media_catalog.replan_coordinator_plan(
                uow.repo,
                user_id,
                row.id,
                requirements,
                persona_id=persona_id,
            )
            uow.repo.add_capability_event(
                row,
                "replanned",
                from_status=row.status,
                to_status=row.status,
                detail={
                    "previous_plan_status": previous_plan_status,
                    "media_plan_status": plan.status,
                    "block_code": plan.block_code,
                    "originating_persona_id_adopted": adopted_legacy_persona_id,
                },
            )
            return self._response(uow.repo, row)

    def events(self, user_id: str, request_id: str) -> list[dict] | None:
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            return [
                {
                    "id": event.id,
                    "capability_request_id": event.capability_request_id,
                    "action": event.action,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "detail": _json_object(event.detail_json),
                    "created_at": event.created_at,
                }
                for event in uow.repo.capability_events(user_id, request_id)
            ]

    def prepare_planned_requests(
        self,
        repo,
        *,
        user_id: str,
        chat_id: str,
        turn_id: str,
        planned: list[PlannedCapability],
    ) -> list[dict]:
        chat = repo.chat(user_id, chat_id)
        if not chat:
            raise NotFoundError("chat not found")
        prepared = []
        for index, request in enumerate(planned):
            definition = self.registry.by_key(request.capability_key)
            requirements = self.registry.requirements(definition, {"prompt": request.prompt})
            requirements = requirements.__class__(
                kind=requirements.kind,
                prompt=requirements.prompt,
                operation=request.operation,
                domains=request.domains,
                content_tags=request.content_tags,
                required_features=request.required_features,
            )
            row, created = repo.add_capability_request(
                user_id=user_id,
                chat_id=chat_id,
                turn_id=turn_id,
                capability_key=definition.key,
                arguments=requirements.as_arguments(),
                status="pending_confirmation",
                permission_mode="confirm",
                idempotency_key=f"turn:{turn_id}:task:{index}:{definition.key}",
            )
            if created:
                plan = self.media_catalog.create_coordinator_plan(
                    repo,
                    user_id,
                    row.id,
                    {
                        "kind": requirements.kind,
                        "operation": requirements.operation,
                        "domains": requirements.domains,
                        "content_tags": requirements.content_tags,
                        "required_features": requirements.required_features,
                    },
                    persona_id=chat.persona_id,
                )
                repo.add_capability_event(
                    row,
                    "requested",
                    from_status=None,
                    to_status="pending_confirmation",
                    detail={"source": "task_model", "media_plan_status": plan.status},
                )
            prepared.append(self._response(repo, row))
        return prepared

    def start_explicit(
        self,
        kind: str,
        user_id: str,
        values: dict,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        definition = self.registry.by_kind(kind)
        if values.get("base_url") and self.provider_url_policy:
            try:
                values = dict(values)
                values["base_url"] = self.provider_url_policy.normalize(
                    values["base_url"],
                    label="Local media service",
                )
            except ValueError as exc:
                raise RequestError(str(exc), 400) from exc
        requirements = self.registry.requirements(definition, {"prompt": values.get("prompt")})
        execution_arguments = requirements.as_arguments()
        allowed_options = {
            "provider",
            "model",
            "size",
            "quality",
            "seconds",
            "backend",
            "base_url",
            "input_reference",
        }
        execution_arguments.update({key: values[key] for key in allowed_options if values.get(key) is not None})
        chat_id = values.get("chat_id")
        durable_idempotency_key = (
            f"explicit:{idempotency_key}" if idempotency_key else f"explicit:{secrets.token_hex(16)}"
        )
        submit = False
        submission_values = dict(execution_arguments)
        with self._uow() as uow:
            if chat_id and not uow.repo.chat(user_id, chat_id):
                raise NotFoundError("chat not found")
            row, created = uow.repo.add_capability_request(
                user_id=user_id,
                chat_id=chat_id,
                turn_id=None,
                capability_key=definition.key,
                arguments=execution_arguments,
                status="queued",
                permission_mode="explicit",
                idempotency_key=durable_idempotency_key,
            )
            if not created and (
                row.capability_key != definition.key
                or row.chat_id != chat_id
                or _json_object(row.arguments_json) != execution_arguments
            ):
                raise ConflictError("idempotency key was already used for a different capability request")
            job = uow.repo.job_for_capability(row.id)
            if created:
                plan = self.media_catalog.create_manual_plan(
                    repo=uow.repo, user_id=user_id, capability_request_id=row.id, kind=kind
                )
                uow.repo.add_capability_event(
                    row,
                    "requested",
                    from_status=None,
                    to_status="queued",
                    detail={"source": "explicit_user_action"},
                )
            else:
                plan = uow.repo.media_execution_plan_for_capability(user_id, row.id)
            if plan:
                submission_values["_media_plan_id"] = plan.id
                submission_values["_operation"] = "generate"
            if not job:
                job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=None,
                    kind=kind,
                    progress="Queued",
                    capability_request_id=row.id,
                )
                uow.repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
                submit = True
            response = self._response(uow.repo, row, job=job)
        if submit:
            self._submit(response["id"], job.id, definition.kind, user_id, chat_id, submission_values)
        return response

    def start_edit(
        self,
        user_id: str,
        values: dict,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        operation = str(values.get("operation") or "").lower()
        if operation not in {"image_to_image", "inpaint", "outpaint"}:
            raise RequestError("unsupported image editing operation", 400)
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise RequestError("Capability prompt required.", 400)
        arguments = {
            "prompt": prompt,
            "operation": operation,
            "source_media_id": str(values.get("source_media_id") or ""),
            "domains": list(values.get("domains") or []),
            "content_tags": list(values.get("content_tags") or []),
            "required_features": list(values.get("required_features") or []),
        }
        if values.get("mask_media_id"):
            arguments["mask_media_id"] = str(values["mask_media_id"])
        chat_id = values.get("chat_id")
        durable_key = f"explicit:{idempotency_key}" if idempotency_key else f"explicit:{secrets.token_hex(16)}"
        submit = False
        with self._uow() as uow:
            if chat_id and not uow.repo.chat(user_id, chat_id):
                raise NotFoundError("chat not found")
            row, created = uow.repo.add_capability_request(
                user_id=user_id,
                chat_id=chat_id,
                turn_id=None,
                capability_key="media.edit_image",
                arguments=arguments,
                status="queued",
                permission_mode="explicit",
                idempotency_key=durable_key,
            )
            if not created and (_json_object(row.arguments_json) != arguments or row.chat_id != chat_id):
                raise ConflictError("idempotency key was already used for a different capability request")
            job = uow.repo.job_for_capability(row.id)
            if created:
                plan = self.media_catalog.create_edit_plan(
                    uow.repo,
                    user_id,
                    row.id,
                    {
                        "kind": "image",
                        "operation": operation,
                        "domains": arguments["domains"],
                        "content_tags": arguments["content_tags"],
                        "required_features": arguments["required_features"],
                    },
                )
                if plan.status != "ready":
                    raise ConflictError(plan.block_message or "No compatible image editing workflow is available.")
                uow.repo.add_capability_event(
                    row,
                    "requested",
                    from_status=None,
                    to_status="queued",
                    detail={"source": "explicit_user_edit", "operation": operation},
                )
            execution_spec = self.media_catalog.execution_spec(uow.repo, user_id, row.id)
            submission_values = dict(arguments)
            submission_values.update(execution_spec["options"])
            submission_values["_estimated_vram_mb"] = execution_spec["estimated_vram_mb"]
            if not job:
                job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=chat_id,
                    turn_id=None,
                    kind="image",
                    progress="Queued",
                    capability_request_id=row.id,
                )
                uow.repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
                submit = True
            response = self._response(uow.repo, row, job=job)
        if submit:
            self._submit(row.id, job.id, "image", user_id, chat_id, submission_values)
        return response

    def approve(self, user_id: str, request_id: str) -> dict | None:
        submit = False
        kind = ""
        values = {}
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            definition = self.registry.by_key(row.capability_key)
            kind = definition.kind
            job = uow.repo.job_for_capability(row.id)
            values = _json_object(row.arguments_json)
            if row.status == "pending_confirmation":
                execution_spec = self.media_catalog.execution_spec(uow.repo, user_id, row.id)
                values.update(execution_spec["options"])
                values["_estimated_vram_mb"] = execution_spec["estimated_vram_mb"]
                transition_capability(uow.repo, row, "queued", "approved")
                job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=row.chat_id,
                    turn_id=None,
                    kind=kind,
                    progress="Queued",
                    capability_request_id=row.id,
                )
                uow.repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
                submit = True
            elif row.status in {"queued", "running", "completed"}:
                pass
            else:
                raise ConflictError(f"capability request is {row.status}")
            response = self._response(uow.repo, row, job=job)
            chat_id = row.chat_id
        if submit:
            self._submit(request_id, job.id, kind, user_id, chat_id, values)
        return response

    def deny(self, user_id: str, request_id: str) -> dict | None:
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            if row.status == "pending_confirmation":
                transition_capability(uow.repo, row, "denied", "denied")
            elif row.status != "denied":
                raise ConflictError(f"capability request is {row.status}")
            return self._response(uow.repo, row)

    def cancel(self, user_id: str, request_id: str) -> dict | None:
        job_id = None
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            job = uow.repo.job_for_capability(row.id)
            if row.status == "pending_confirmation":
                transition_capability(uow.repo, row, "cancelled", "cancelled")
            elif row.status in {"queued", "running"}:
                job_id = job.id if job else None
            elif row.status != "cancelled":
                return self._response(uow.repo, row, job=job)
            response = self._response(uow.repo, row, job=job)
        if job_id:
            self.jobs.cancel(user_id, job_id)
            return self.get(user_id, request_id)
        return response

    def _submit(self, request_id: str, job_id: str, kind: str, user_id: str, chat_id: str | None, values: dict):
        arguments = dict(values)
        prompt = str(arguments.pop("prompt", "")).strip()
        operation = arguments.pop("operation", None)
        if operation:
            arguments["_operation"] = operation
        for key in ("domains", "content_tags", "required_features", "source_media_id", "mask_media_id"):
            arguments.pop(key, None)
        estimated_vram_mb = max(0, int(arguments.pop("_estimated_vram_mb", 0) or 0))

        def on_start(repo):
            row = repo.capability_request_by_id(request_id)
            if row and row.status == "queued":
                transition_capability(repo, row, "running", "started")

        def on_success(repo, result):
            row = repo.capability_request_by_id(request_id)
            if row and row.status == "running":
                transition_capability(repo, row, "completed", "completed", result=result or {})
            return result

        def on_failure(repo, code, message):
            row = repo.capability_request_by_id(request_id)
            if row and row.status not in CAPABILITY_TERMINAL_STATES:
                transition_capability(repo, row, "failed", "failed", code=code, message=message)

        def on_cancel(repo):
            row = repo.capability_request_by_id(request_id)
            if row and row.status not in CAPABILITY_TERMINAL_STATES:
                transition_capability(repo, row, "cancelled", "cancelled")

        try:
            resource_request = (
                self.jobs.resource_coordinator.request_for_media(user_id, kind, arguments, estimated_vram_mb)
                if self.jobs.resource_coordinator
                else None
            )
            self.jobs.submit(
                job_id=job_id,
                job_type=kind,
                user_id=user_id,
                chat_id=chat_id,
                turn_id=None,
                latency_class="bulk" if kind == "video" else "standard",
                model_key=f"{kind}:{arguments.get('model') or ''}",
                execution=JobExecution(
                    execute=lambda token: self.media.generate(kind, user_id, chat_id, prompt, token, arguments),
                    on_start=on_start,
                    on_success=on_success,
                    on_failure=on_failure,
                    on_cancel=on_cancel,
                ),
                estimated_vram_mb=estimated_vram_mb,
                resource_request=resource_request,
            )
        except Exception:
            self.jobs.fail_unsubmitted(job_id, "The capability could not be submitted.", on_failure=on_failure)
            raise

    def _response(self, repo, row, *, job=None) -> dict:
        if not row:
            raise NotFoundError()
        job = job or repo.job_for_capability(row.id)
        turn = repo.turn_by_id(row.turn_id) if row.turn_id else None
        error = None
        if row.error_code or row.error_message:
            error = {"code": row.error_code or "failed", "message": row.error_message or "Capability failed."}
        return {
            "id": row.id,
            "capability_key": row.capability_key,
            "status": row.status,
            "permission_mode": row.permission_mode,
            "arguments": _json_object(row.arguments_json),
            "result": _json_object(row.result_json) if row.result_json else None,
            "error": error,
            "chat_id": row.chat_id,
            "turn_id": row.turn_id,
            "assistant_message_id": turn.assistant_message_id if turn else None,
            "job_id": job.id if job else None,
            "requested_at": row.requested_at,
            "decided_at": row.decided_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "expires_at": row.expires_at,
            "media_plan": self.media_catalog.plan_for_capability(repo, row.user_id, row.id),
        }
