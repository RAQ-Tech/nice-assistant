from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import secrets
import time

from app.identity_conditioning import prompt_with_identity_description, public_identity_conditioning
from app.media import (
    normalize_image_quality,
    normalize_image_size,
    normalize_local_image_backend,
    normalize_video_model,
    normalize_video_seconds,
    normalize_video_size,
    user_safe_image_error,
    user_safe_video_error,
)
from app.provider_contracts import CancellationToken, MediaRequest, ProviderError
from app.repositories import UnitOfWork, now_ts
from app.service_errors import RequestError
from app.storage import write_artifact_atomic


class MediaService:
    def __init__(
        self,
        session_factory,
        secret_store,
        config,
        registry,
        identity,
        logger,
        provider_url_policy=None,
        metrics=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.registry = registry
        self.identity = identity
        self.logger = logger
        self.provider_url_policy = provider_url_policy
        self.metrics = metrics

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def generate(
        self,
        kind: str,
        user_id: str,
        chat_id: str | None,
        prompt: str,
        cancellation: CancellationToken,
        values: dict,
    ) -> dict:
        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {"preferences": {}}
        preferences = settings.get("preferences") or {}
        if kind == "image":
            return self._generate_image(user_id, chat_id, prompt, values, settings, preferences, cancellation)
        if kind == "video":
            return self._generate_video(user_id, chat_id, prompt, values, settings, preferences, cancellation)
        raise RequestError("unsupported media kind", 400)

    def _generate_image(self, user_id, chat_id, prompt, values, settings, preferences, cancellation):
        identity = values.get("_identity_conditioning")
        conditioned_identity = identity if (identity or {}).get("status") == "ready" else None
        generation_plan_id = values.get("_media_plan_id")
        prompt = prompt_with_identity_description(prompt, identity)
        max_attempts = int((conditioned_identity or {}).get("max_generation_attempts") or 1)
        candidates = []
        attempt_values = dict(values)
        for attempt_number in range(1, max_attempts + 1):
            cancellation.raise_if_cancelled()
            operation = str(attempt_values.get("_operation") or "generate")
            attempt = self._start_attempt(
                user_id,
                generation_plan_id,
                attempt_number,
                operation,
                attempt_values.get("_source_media_id"),
                (conditioned_identity or {}).get("correction_workflow_resource_id")
                if operation == "image_to_image"
                else (conditioned_identity or {}).get("workflow_resource_id"),
            )
            try:
                artifact = self._generate_image_artifact(
                    prompt, attempt_values, settings, preferences, conditioned_identity, cancellation
                )
                media = self._persist_image(user_id, chat_id, generation_plan_id, artifact, cancellation)
                if not conditioned_identity:
                    self._finish_attempt(attempt, "passed", media_id=media.id)
                    return self._image_result(media, chat_id, identity)
                validation = self.identity.validate_generated_media(
                    user_id,
                    media.id,
                    conditioned_identity,
                    cancellation,
                )
                validation_row = validation.get("validation") or {}
                status = validation["status"]
                attempt_status = status if status in {"passed", "failed"} else "unverified"
                self._finish_attempt(
                    attempt,
                    attempt_status,
                    media_id=media.id,
                    validation_id=validation_row.get("id"),
                    score=validation_row.get("score"),
                    threshold=validation_row.get("threshold") or conditioned_identity.get("acceptance_threshold"),
                )
                candidate = (media, validation)
                candidates.append(candidate)
                if status == "passed":
                    return self._image_result(media, chat_id, conditioned_identity, validation, attempt_number)
                if status != "failed":
                    return self._image_result(media, chat_id, conditioned_identity, validation, attempt_number)
                if attempt_number < max_attempts:
                    attempt_values = self._correction_values(values, conditioned_identity, media)
                    continue
                if conditioned_identity.get("failure_policy") == "show_unverified":
                    best_media, best_validation = max(
                        candidates,
                        key=lambda item: float(((item[1].get("validation") or {}).get("score")) or -1),
                    )
                    return self._image_result(
                        best_media,
                        chat_id,
                        conditioned_identity,
                        best_validation,
                        attempt_number,
                    )
                raise ProviderError(
                    provider="identity-verifier",
                    code="identity_validation_failed",
                    user_message=(
                        "The generated image did not meet this persona's identity threshold and was not shown."
                    ),
                )
            except ProviderError as exc:
                if attempt and not self._attempt_terminal(attempt):
                    self._finish_attempt(
                        attempt,
                        "cancelled" if exc.code == "cancelled" else "error",
                        error_code=exc.code,
                        error_message=exc.user_message,
                    )
                raise
            except Exception as exc:
                if attempt and not self._attempt_terminal(attempt):
                    self._finish_attempt(
                        attempt,
                        "error",
                        error_code="image_generation_failed",
                        error_message="Image generation failed.",
                    )
                raise
        raise ProviderError(
            provider="identity-verifier",
            code="identity_validation_failed",
            user_message="No generated image met the persona identity threshold.",
        )

    def _generate_image_artifact(self, prompt, values, settings, preferences, identity, cancellation):
        selected = str(values.get("provider") or preferences.get("image_provider") or "disabled").lower()
        if selected == "disabled":
            raise RequestError("Image generation is disabled. Enable an image provider in Settings.", 409)
        backend_override = None
        if selected == "local/automatic1111":
            selected = "local"
        elif selected == "local/comfyui":
            selected = "local"
            backend_override = "comfyui"
        size = normalize_image_size(values.get("size") or preferences.get("image_size") or "1024x1024")
        quality = normalize_image_quality(values.get("quality") or preferences.get("image_quality") or "auto")
        options = {}
        try:
            if selected == "openai":
                key = settings.get("openai_api_key")
                if not key:
                    raise RequestError("OPENAI API key missing", 400)
                provider = self.registry.media("openai-image")
                options = {"api_key": key, "size": size, "quality": quality}
            elif selected == "local":
                backend = normalize_local_image_backend(
                    backend_override or values.get("backend") or preferences.get("image_local_backend")
                )
                if identity and backend != "comfyui":
                    raise RequestError("Persona identity conditioning requires a configured ComfyUI workflow.", 409)
                provider = self.registry.media("local-image")
                default_url = (
                    self.config.comfyui_base_url if backend == "comfyui" else self.config.automatic1111_base_url
                )
                base_url = values.get("base_url") or preferences.get("image_local_base_url") or default_url
                if self.provider_url_policy:
                    base_url = self.provider_url_policy.normalize(base_url, label="Local image service")
                workflow_patch = values.get("workflow_patch")
                additional_parameters = (
                    json.dumps(workflow_patch, separators=(",", ":"), ensure_ascii=False)
                    if isinstance(workflow_patch, dict)
                    else preferences.get("image_local_additional_parameters")
                )
                options = {
                    "operation": values.get("_operation") or "generate",
                    "backend": backend,
                    "base_url": base_url,
                    "size": values.get("size") or preferences.get("image_size") or size,
                    "quality": quality,
                    "allow_nsfw": bool(
                        values.get("allow_nsfw")
                        if values.get("allow_nsfw") is not None
                        else preferences.get("image_local_allow_nsfw", False)
                    ),
                    "local_settings": {
                        "steps": values.get("steps") or preferences.get("image_local_steps"),
                        "cfg_scale": values.get("cfg_scale") or preferences.get("image_local_cfg_scale"),
                        "sampler_name": values.get("sampler_name") or preferences.get("image_local_sampler_name"),
                        "scheduler": values.get("scheduler") or preferences.get("image_local_scheduler"),
                        "seed": preferences.get("image_local_seed"),
                        "model": values.get("model") if "model" in values else preferences.get("image_local_model"),
                        "api_auth": preferences.get("image_local_api_auth"),
                        "additional_parameters": additional_parameters,
                        "loras": values.get("loras") or [],
                        "identity_reference_path": values.get("_identity_reference_path"),
                        "identity_reference_sha256": values.get("_identity_reference_sha256"),
                        "identity_reference_bindings": values.get("identity_image_bindings") or [],
                        "identity_image_bindings": values.get("identity_image_bindings") or [],
                        "source_image_path": values.get("_source_image_path"),
                        "source_image_sha256": values.get("_source_image_sha256"),
                        "source_image_bindings": values.get("source_image_bindings") or [],
                        "mask_image_path": values.get("_mask_image_path"),
                        "mask_image_sha256": values.get("_mask_image_sha256"),
                        "mask_image_bindings": values.get("mask_image_bindings") or [],
                    },
                }
            else:
                raise RequestError(f"Image provider '{selected}' is not recognized by the server.", 400)
            started = time.monotonic()
            outcome = "failed"
            try:
                artifact = provider.generate(MediaRequest("image", prompt, options), cancellation)
                outcome = "completed"
                return artifact
            finally:
                if self.metrics:
                    self.metrics.provider(
                        getattr(provider, "name", selected),
                        "image",
                        "cancelled" if cancellation.cancelled else outcome,
                        int((time.monotonic() - started) * 1000),
                    )
        except RequestError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            safe, _detail, request_id = user_safe_image_error(
                exc,
                "local/comfyui" if selected == "local" and options.get("backend") == "comfyui" else selected,
            )
            raise ProviderError(
                provider=selected,
                code="image_generation_failed",
                user_message=safe,
                retryable=True,
                request_id=request_id or None,
            ) from exc

    def _persist_image(self, user_id, chat_id, generation_plan_id, artifact, cancellation):
        filename = f"{user_id}_{secrets.token_hex(8)}{artifact.extension}"
        target = self.config.image_dir / filename
        cancellation.raise_if_cancelled()
        try:
            write_artifact_atomic(target, artifact.content)
            cancellation.raise_if_cancelled()
            with self._uow() as uow:
                media = uow.repo.add_media(
                    user_id=user_id,
                    chat_id=chat_id,
                    kind="image",
                    filename=filename,
                    local_path=str(target),
                    generation_plan_id=generation_plan_id,
                )
                cancellation.raise_if_cancelled()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return media

    @staticmethod
    def _image_result(media, chat_id, identity=None, validation=None, attempts=1):
        canonical_url = f"/api/v1/media/{media.id}"
        unconditioned = (identity or {}).get("status") == "unconditioned"
        message = "Here is your generated image."
        if unconditioned:
            message += (
                " The approved persona reference was not applied, so resemblance is not guaranteed; "
                "this result is unconditioned and unverified."
            )
        result = {
            "ok": True,
            "text": f"{message}\n\n![Generated image]({canonical_url})",
            "imageUrl": canonical_url,
            "mediaId": media.id,
            "chatId": chat_id,
        }
        validation = validation or {}
        conditioning = public_identity_conditioning(
            identity,
            applied=True,
            verification_status=validation.get("status"),
            claim_status=validation.get("claim_status"),
        )
        if conditioning:
            result["identityConditioning"] = conditioning
            if conditioning.get("status") == "conditioned":
                result["identityWorkflow"] = {"attempts": attempts, "validation": validation.get("validation")}
        return result

    def _start_attempt(self, user_id, plan_id, number, operation, source_media_id, workflow_resource_id):
        if not plan_id:
            return None
        with self._uow() as uow:
            return uow.repo.add_media_generation_attempt(
                user_id=user_id,
                media_plan_id=plan_id,
                attempt_number=number,
                operation=operation,
                source_media_id=source_media_id,
                workflow_resource_id=workflow_resource_id,
            ).id

    def _attempt_terminal(self, attempt_id) -> bool:
        if not attempt_id:
            return True
        with self._uow() as uow:
            row = uow.repo.media_generation_attempt_by_id(attempt_id)
            return not row or row.status != "running"

    def _finish_attempt(self, attempt_id, status, **values):
        if not attempt_id:
            return
        with self._uow() as uow:
            row = uow.repo.media_generation_attempt_by_id(attempt_id)
            if not row or row.status != "running":
                return
            row.status = status
            row.media_id = values.get("media_id")
            row.validation_id = values.get("validation_id")
            row.score = values.get("score")
            row.threshold = values.get("threshold")
            row.error_code = values.get("error_code")
            row.error_message = str(values.get("error_message") or "")[:500] or None
            row.completed_at = now_ts()

    @staticmethod
    def _correction_values(base_values: dict, identity: dict, media) -> dict:
        values = dict(base_values)
        patch = identity.get("correction_workflow_patch")
        if isinstance(patch, dict) and patch and identity.get("correction_source_image_bindings"):
            values["_operation"] = "image_to_image"
            values["workflow_patch"] = patch
            values["source_image_bindings"] = identity["correction_source_image_bindings"]
            values["identity_image_bindings"] = identity.get("correction_identity_image_bindings") or values.get(
                "identity_image_bindings"
            )
            values["_source_media_id"] = media.id
            values["_source_image_path"] = media.local_path
            values["_source_image_sha256"] = sha256(Path(media.local_path).read_bytes()).hexdigest()
        return values

    def _generate_video(self, user_id, chat_id, prompt, values, settings, preferences, cancellation):
        generation_plan_id = values.get("_media_plan_id")
        selected = str(values.get("provider") or preferences.get("video_provider") or "disabled").lower()
        if selected == "disabled":
            raise RequestError("Video generation is disabled. Enable a video provider in Settings.", 409)
        if selected != "openai":
            raise RequestError(f"Video provider '{selected}' is not recognized by the server.", 400)
        key = settings.get("openai_api_key")
        if not key:
            raise RequestError("OPENAI API key missing", 400)
        model = normalize_video_model(values.get("model") or preferences.get("video_model"))
        options = {
            "api_key": key,
            "model": model,
            "seconds": normalize_video_seconds(values.get("seconds") or preferences.get("video_seconds")),
            "size": normalize_video_size(values.get("size") or preferences.get("video_size"), model),
            "input_reference": values.get("input_reference"),
        }
        started = time.monotonic()
        outcome = "failed"
        try:
            artifact = self.registry.media("openai-video").generate(
                MediaRequest("video", prompt, options), cancellation
            )
            outcome = "completed"
        except ProviderError:
            raise
        except Exception as exc:
            safe, _detail, request_id = user_safe_video_error(exc)
            raise ProviderError(
                provider="openai",
                code="video_generation_failed",
                user_message=safe,
                retryable=True,
                request_id=request_id or None,
            ) from exc
        finally:
            if self.metrics:
                self.metrics.provider(
                    "openai-video",
                    "video",
                    "cancelled" if cancellation.cancelled else outcome,
                    int((time.monotonic() - started) * 1000),
                )
        filename = f"{user_id}_{secrets.token_hex(8)}{artifact.extension}"
        target = self.config.video_dir / filename
        cancellation.raise_if_cancelled()
        try:
            write_artifact_atomic(target, artifact.content)
            cancellation.raise_if_cancelled()
            with self._uow() as uow:
                media = uow.repo.add_media(
                    user_id=user_id,
                    chat_id=chat_id,
                    kind="video",
                    filename=filename,
                    local_path=str(target),
                    generation_plan_id=generation_plan_id,
                )
                cancellation.raise_if_cancelled()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        canonical_url = f"/api/v1/media/{media.id}"
        return {
            "ok": True,
            "text": f"Here is your generated video.\n\n[Download generated video]({canonical_url})",
            "videoUrl": canonical_url,
            "mediaId": media.id,
            "chatId": chat_id,
        }
