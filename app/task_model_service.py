from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
import math
import time
from typing import Any

from app.auth import redact_sensitive_text
from app.provider_contracts import CancellationToken, ChatRequest, ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import NotFoundError, RequestError
from app.task_contracts import TASK_DEFINITIONS, TaskContractError, task_definition


@dataclass(frozen=True)
class TaskExecutionResult:
    role: str
    output: Any
    run_id: str
    provider: str | None
    model: str | None
    fallback_used: bool


class TaskModelService:
    """Runs typed, owner-scoped platform tasks without retaining prompt or output content."""

    def __init__(self, session_factory, secret_store, providers, logger):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.providers = providers
        self.logger = logger

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def profiles(self, user_id: str) -> list[dict]:
        with self._uow() as uow:
            return [self._profile_response(row) for row in uow.repo.task_model_profiles(user_id)]

    def update_profile(self, user_id: str, role: str, values: dict) -> dict:
        definition = self._definition(role)
        normalized = self._normalize_profile(values)
        provider = normalized.get("provider")
        fallback_provider = normalized.get("fallback_provider")
        if provider and provider not in self.providers.chat_providers:
            raise RequestError("task model provider is not configured", 400)
        if fallback_provider and fallback_provider not in self.providers.chat_providers:
            raise RequestError("task model fallback provider is not configured", 400)
        if normalized.get("fallback_policy") == "deterministic" and role != "title_generation":
            raise RequestError("deterministic fallback is available only for chat titles", 400)
        with self._uow() as uow:
            if not uow.repo.user(user_id):
                raise NotFoundError()
            row = uow.repo.save_task_model_profile(user_id, definition.role, normalized)
            return self._profile_response(row)

    def readiness(self, user_id: str, role: str) -> dict:
        self._definition(role)
        with self._uow() as uow:
            profile = uow.repo.task_model_profile(user_id, role)
            if not profile:
                raise NotFoundError("task model profile not found")
            response = self._profile_response(profile)
        if not response["enabled"]:
            return {
                "role": role,
                "ready": False,
                "status": "disabled",
                "message": "This task role is disabled and will use its documented fallback behavior.",
                "primary_ready": False,
                "fallback_ready": False,
                "effective_model": None,
            }
        primary = self._attempt_readiness(response["provider"], response["model"])
        fallback = None
        fallback_provider = response.get("fallback_provider")
        fallback_model = response.get("fallback_model")
        if fallback_provider or fallback_model:
            fallback = self._attempt_readiness(
                fallback_provider or response["provider"],
                fallback_model,
            )
        ready = bool(primary["ready"] or (fallback and fallback["ready"]))
        return {
            "role": role,
            "ready": ready,
            "status": "ready" if primary["ready"] else ("fallback_ready" if ready else "unavailable"),
            "message": primary["message"] if primary["ready"] or not fallback else fallback["message"],
            "primary_ready": bool(primary["ready"]),
            "fallback_ready": bool(fallback and fallback["ready"]),
            "effective_model": primary.get("effective_model") if primary["ready"] else None,
            "fallback_effective_model": fallback.get("effective_model") if fallback and fallback["ready"] else None,
        }

    def runs(self, user_id: str, *, role: str | None = None, limit: int = 50) -> list[dict]:
        if role:
            self._definition(role)
        with self._uow() as uow:
            return [
                self._run_response(row)
                for row in uow.repo.task_model_runs(user_id, role=role, limit=max(1, min(200, limit)))
            ]

    def run(
        self,
        user_id: str,
        role: str,
        task_input: Any,
        cancellation: CancellationToken,
        *,
        chat_id: str | None = None,
        turn_id: str | None = None,
    ) -> TaskExecutionResult:
        definition = self._definition(role)
        messages = definition.messages(task_input)
        input_tokens = self._estimate_tokens(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))
        with self._uow() as uow:
            profile = uow.repo.task_model_profile(user_id, role)
            if not profile:
                raise NotFoundError("task model profile not found")
            run = uow.repo.add_task_model_run(
                user_id=user_id,
                role=role,
                chat_id=chat_id,
                turn_id=turn_id,
                requested_provider=profile.provider,
                requested_model=profile.model,
                input_tokens_estimated=input_tokens,
            )
            profile_data = self._profile_response(profile)
            run_id = run.id

        if cancellation.cancelled:
            self._finish_run(
                run_id,
                status="failed",
                provider=None,
                model=None,
                fallback_used=False,
                code="cancelled",
                message="Request cancelled.",
                attempts=[],
                started=time.monotonic(),
            )
            cancellation.raise_if_cancelled()
        if not profile_data["enabled"]:
            return self._fallback_or_raise(
                definition,
                task_input,
                profile_data,
                run_id,
                code="task_disabled",
                message="Task role is disabled.",
                attempts=[],
                started=time.monotonic(),
            )
        if input_tokens > profile_data["max_input_tokens"]:
            return self._fallback_or_raise(
                definition,
                task_input,
                profile_data,
                run_id,
                code="task_input_budget_exceeded",
                message="Task input exceeded its configured token budget.",
                attempts=[],
                started=time.monotonic(),
            )

        started = time.monotonic()
        attempts = []
        resolved_attempts = self._resolved_attempts(profile_data)
        if not resolved_attempts:
            return self._fallback_or_raise(
                definition,
                task_input,
                profile_data,
                run_id,
                code="task_model_unavailable",
                message="No model is available for this task role.",
                attempts=attempts,
                started=started,
            )

        last_code = "task_model_failed"
        last_message = "The task model failed."
        for index, (provider_name, model) in enumerate(resolved_attempts):
            attempt_started = time.monotonic()
            try:
                cancellation.raise_if_cancelled()
                provider = self.providers.chat(provider_name)
                request = ChatRequest(
                    model=model,
                    messages=messages,
                    options={
                        "num_ctx": max(
                            2048,
                            profile_data["max_input_tokens"] + profile_data["max_output_tokens"] + 256,
                        ),
                        "num_predict": profile_data["max_output_tokens"],
                        "temperature": profile_data["temperature"],
                    },
                    response_format=definition.response_schema(task_input),
                    timeout_seconds=profile_data["timeout_seconds"],
                )
                raw = provider.generate(request, cancellation)
                output = definition.parse_output(raw, task_input, profile_data["max_output_tokens"])
                output_tokens = self._estimate_tokens(json.dumps(self._wire(output), ensure_ascii=False))
                if output_tokens > profile_data["max_output_tokens"]:
                    raise ProviderError(
                        provider="task-model",
                        code="task_output_budget_exceeded",
                        user_message="Task output exceeded its configured token budget.",
                    )
                attempts.append(
                    {
                        "provider": provider_name,
                        "model": model,
                        "status": "completed",
                        "latency_ms": int((time.monotonic() - attempt_started) * 1000),
                    }
                )
                self._finish_run(
                    run_id,
                    status="completed",
                    provider=provider_name,
                    model=model,
                    fallback_used=index > 0,
                    attempts=attempts,
                    output_tokens=output_tokens,
                    started=started,
                )
                return TaskExecutionResult(role, output, run_id, provider_name, model, index > 0)
            except ProviderError as exc:
                if exc.code == "cancelled" or cancellation.cancelled:
                    self._finish_run(
                        run_id,
                        status="failed",
                        provider=provider_name,
                        model=model,
                        fallback_used=index > 0,
                        code="cancelled",
                        message="Request cancelled.",
                        attempts=attempts,
                        started=started,
                    )
                    raise
                last_code = exc.code
                last_message = exc.user_message
            except TaskContractError:
                last_code = "invalid_task_output"
                last_message = "The task model returned an invalid structured result."
            except Exception as exc:  # noqa: BLE001 - normalize unexpected task-provider failures
                self.logger.error(
                    "task model failed role=%s provider=%s model=%s error=%s",
                    role,
                    provider_name,
                    model,
                    exc.__class__.__name__,
                )
                last_code = "task_model_internal_error"
                last_message = "The task model failed unexpectedly."
            attempts.append(
                {
                    "provider": provider_name,
                    "model": model,
                    "status": "failed",
                    "code": last_code,
                    "latency_ms": int((time.monotonic() - attempt_started) * 1000),
                }
            )

        return self._fallback_or_raise(
            definition,
            task_input,
            profile_data,
            run_id,
            code=last_code,
            message=last_message,
            attempts=attempts,
            started=started,
        )

    def _fallback_or_raise(
        self,
        definition,
        task_input,
        profile,
        run_id,
        *,
        code,
        message,
        attempts,
        started,
    ):
        safe = redact_sensitive_text(message or "")[:1000] or "Task model failed."
        if profile["fallback_policy"] == "fail":
            self._finish_run(
                run_id,
                status="failed",
                provider=None,
                model=None,
                fallback_used=len(attempts) > 1,
                code=code,
                message=safe,
                attempts=attempts,
                started=started,
            )
            raise ProviderError(
                provider="task-model",
                code=code,
                user_message=safe,
                retryable=True,
            )
        output = definition.fallback_output(task_input)
        self._finish_run(
            run_id,
            status="fallback",
            provider=None,
            model=None,
            fallback_used=True,
            code=code,
            message=safe,
            attempts=attempts,
            output_tokens=self._estimate_tokens(json.dumps(self._wire(output), ensure_ascii=False)),
            started=started,
        )
        return TaskExecutionResult(definition.role, output, run_id, None, None, True)

    def _resolved_attempts(self, profile: dict) -> list[tuple[str, str]]:
        attempts = []
        primary_model = self._resolve_model(profile["provider"], profile.get("model"))
        if primary_model:
            attempts.append((profile["provider"], primary_model))
        fallback_provider = profile.get("fallback_provider") or (
            profile["provider"] if profile.get("fallback_model") else None
        )
        if fallback_provider:
            fallback_model = self._resolve_model(fallback_provider, profile.get("fallback_model"))
            candidate = (fallback_provider, fallback_model) if fallback_model else None
            if candidate and candidate not in attempts:
                attempts.append(candidate)
        return attempts

    def _resolve_model(self, provider_name: str, configured_model: str | None) -> str | None:
        if configured_model:
            return configured_model
        try:
            models = self.providers.chat(provider_name).list_models()
        except Exception:
            return None
        return models[0] if models else None

    def _attempt_readiness(self, provider_name: str, configured_model: str | None) -> dict:
        try:
            provider = self.providers.chat(provider_name)
        except LookupError:
            return {"ready": False, "message": "The configured provider adapter is unavailable."}
        health = provider.health()
        if not health.ok:
            return {"ready": False, "message": health.message}
        models = provider.list_models()
        effective = configured_model or (models[0] if models else None)
        if not effective:
            return {"ready": False, "message": "The provider has no installed models."}
        if configured_model and configured_model not in models:
            return {"ready": False, "message": "The configured model is not installed."}
        return {"ready": True, "message": "Task model is ready.", "effective_model": effective}

    def _finish_run(
        self,
        run_id: str,
        *,
        status: str,
        provider: str | None,
        model: str | None,
        fallback_used: bool,
        attempts: list,
        started: float,
        code: str | None = None,
        message: str | None = None,
        output_tokens: int | None = None,
    ) -> None:
        with self._uow() as uow:
            row = uow.repo.task_model_run_by_id(run_id)
            if not row or row.status != "running":
                return
            row.status = status
            row.executed_provider = provider
            row.executed_model = model
            row.fallback_used = int(bool(fallback_used))
            row.error_code = code
            row.error_message = redact_sensitive_text(message or "")[:1000] or None
            row.attempts_json = json.dumps(attempts, separators=(",", ":"), ensure_ascii=False)
            row.output_tokens_estimated = output_tokens
            row.latency_ms = max(0, int((time.monotonic() - started) * 1000))
            row.completed_at = now_ts()

    @staticmethod
    def _normalize_profile(values: dict) -> dict:
        result = dict(values)
        for field in ("provider", "model", "fallback_provider", "fallback_model"):
            if field not in result:
                continue
            normalized = str(result[field] or "").strip()
            result[field] = normalized or None
        if "provider" in result and not result["provider"]:
            raise RequestError("task model provider is required", 400)
        return result

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len((text or "").encode("utf-8")) / 3))

    @staticmethod
    def _wire(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        return value

    @staticmethod
    def _definition(role: str):
        try:
            return task_definition(role)
        except TaskContractError as exc:
            raise RequestError("unsupported task model role", 404) from exc

    @staticmethod
    def _profile_response(row) -> dict:
        definition = TASK_DEFINITIONS[row.role]
        return {
            "role": row.role,
            "title": definition.title,
            "description": definition.description,
            "enabled": bool(row.enabled),
            "provider": row.provider,
            "model": row.model,
            "fallback_provider": row.fallback_provider,
            "fallback_model": row.fallback_model,
            "max_input_tokens": row.max_input_tokens,
            "max_output_tokens": row.max_output_tokens,
            "timeout_seconds": row.timeout_seconds,
            "temperature": row.temperature,
            "fallback_policy": row.fallback_policy,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _run_response(row) -> dict:
        try:
            attempts = json.loads(row.attempts_json or "[]")
        except (TypeError, ValueError):
            attempts = []
        return {
            "id": row.id,
            "role": row.role,
            "chat_id": row.chat_id,
            "turn_id": row.turn_id,
            "requested_provider": row.requested_provider,
            "requested_model": row.requested_model,
            "executed_provider": row.executed_provider,
            "executed_model": row.executed_model,
            "status": row.status,
            "fallback_used": bool(row.fallback_used),
            "error": (
                {"code": row.error_code or "failed", "message": row.error_message or "Task model failed."}
                if row.error_code or row.error_message
                else None
            ),
            "attempts": attempts if isinstance(attempts, list) else [],
            "input_tokens_estimated": row.input_tokens_estimated,
            "output_tokens_estimated": row.output_tokens_estimated,
            "latency_ms": row.latency_ms,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }
