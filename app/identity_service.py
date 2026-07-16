from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import secrets

from app.auth import redact_sensitive_text
from app.compreface_identity_provider import normalize_compreface_base_url
from app.identity_conditioning import public_identity_conditioning
from app.identity_contracts import IdentityVerificationRequest
from app.identity_images import (
    MAX_CANDIDATE_BYTES,
    MAX_REFERENCE_BYTES,
    normalize_identity_image,
    read_identity_image_file,
)
from app.job_service import JobExecution, job_response
from app.provider_contracts import CancellationToken, ProviderError, ProviderStatus
from app.repositories import UnitOfWork, now_ts
from app.service_errors import ConflictError, NotFoundError, RequestError
from app.storage import write_artifact_atomic


MASKED_SECRET = "********"


def _masked(value: str) -> str:
    return f"{MASKED_SECRET}{value[-4:]}" if value else ""


def _is_masked(value: str | None) -> bool:
    return not value or str(value).startswith(MASKED_SECRET)


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class IdentityService:
    def __init__(self, session_factory, secret_store, config, jobs, providers: dict, logger, provider_url_policy=None):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.jobs = jobs
        self.providers = providers
        self.logger = logger
        self.provider_url_policy = provider_url_policy

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def settings(self, user_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.identity_settings(user_id)
            return self._settings_response(row)

    def save_settings(self, user_id: str, values: dict) -> dict:
        provider = str(values.get("provider") or "disabled").lower()
        if provider not in {"disabled", "compreface"}:
            raise RequestError("Unsupported visual identity provider.", 400)
        base_url = str(values.get("base_url") or "").strip()
        if provider == "compreface":
            try:
                base_url = normalize_compreface_base_url(base_url)
                if self.provider_url_policy:
                    base_url = self.provider_url_policy.normalize(base_url, label="CompreFace")
            except ValueError as exc:
                raise RequestError(str(exc), 400) from exc
        timeout = float(values.get("timeout_seconds") or 15)
        if timeout < 1 or timeout > 120:
            raise RequestError("Identity verifier timeout must be between 1 and 120 seconds.", 400)
        api_key = values.get("api_key")
        preserve = _is_masked(api_key)
        if provider == "compreface" and not preserve and not str(api_key).strip():
            raise RequestError("A CompreFace API key is required.", 400)
        with self._uow() as uow:
            current = uow.repo.identity_settings(user_id)
            if provider == "compreface" and preserve and not (current and current.api_key_encrypted):
                raise RequestError("A CompreFace API key is required.", 400)
            row = uow.repo.save_identity_settings(
                user_id,
                {
                    "provider": provider,
                    "base_url": base_url or None,
                    "api_key": api_key,
                    "timeout_seconds": timeout,
                },
                preserve_secret=preserve,
            )
            return self._settings_response(row)

    def check_provider(self, user_id: str) -> dict:
        settings = self._provider_settings(user_id)
        if settings["provider"] == "disabled":
            return {
                "provider": "disabled",
                "status": "unavailable",
                "ready": False,
                "message": "Visual identity validation is disabled.",
            }
        provider = self.providers.get(settings["provider"])
        if not provider:
            raise ConflictError("The configured visual identity provider is not installed.")
        health = provider.health(settings["base_url"], settings["api_key"], settings["timeout_seconds"])
        return {
            "provider": health.provider,
            "status": health.status.value,
            "ready": health.status is ProviderStatus.READY,
            "message": health.message,
            "latency_ms": health.latency_ms,
        }

    def get_profile(self, user_id: str, persona_id: str) -> dict:
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=False)
            if not identity:
                generation_configured, verification_configured = self._configuration_readiness(uow.repo, user_id)
                return self._empty_profile(
                    persona_id,
                    generation_workflow_configured=generation_configured,
                    verification_configured=verification_configured,
                )
            return self._profile_response(uow.repo, identity)

    def save_profile(self, user_id: str, persona_id: str, values: dict) -> dict:
        threshold = float(values.get("acceptance_threshold", 0.78))
        attempts = int(values.get("max_generation_attempts", 2))
        policy = str(values.get("failure_policy") or "show_unverified")
        conditioning_fallback = str(values.get("conditioning_fallback") or "allow_unconditioned")
        if threshold < 0 or threshold > 1:
            raise RequestError("Acceptance threshold must be between 0 and 1.", 400)
        if attempts < 1 or attempts > 10:
            raise RequestError("Maximum generation attempts must be between 1 and 10.", 400)
        if policy not in {"block_claim", "show_unverified"}:
            raise RequestError("Unsupported identity failure policy.", 400)
        if conditioning_fallback not in {"allow_unconditioned", "require_conditioning"}:
            raise RequestError("Unsupported identity conditioning fallback.", 400)
        description = str(values.get("appearance_description") or "").strip()[:8000]
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=True)
            identity.appearance_description = description or None
            identity.acceptance_threshold = threshold
            identity.max_generation_attempts = attempts
            identity.failure_policy = policy
            identity.conditioning_fallback = conditioning_fallback
            identity.revision += 1
            identity.updated_at = now_ts()
            uow.repo.add_identity_event(identity, "profile_updated", detail={"revision": identity.revision})
            return self._profile_response(uow.repo, identity)

    def grant_consent(self, user_id: str, persona_id: str, attested: bool) -> dict:
        if not attested:
            raise RequestError("Consent and the right to use the reference images must be attested.", 400)
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=True)
            stamp = now_ts()
            identity.consent_status = "granted"
            identity.consent_granted_at = stamp
            identity.consent_withdrawn_at = None
            identity.status = "active" if uow.repo.approved_identity_references(user_id, identity.id) else "draft"
            identity.revision += 1
            identity.updated_at = stamp
            uow.repo.add_identity_event(identity, "consent_granted")
            return self._profile_response(uow.repo, identity)

    def withdraw_consent(self, user_id: str, persona_id: str) -> dict:
        paths: list[Path] = []
        job_ids: list[str] = []
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=False)
            if not identity:
                raise NotFoundError("visual identity profile not found")
            stamp = now_ts()
            identity.consent_status = "withdrawn"
            identity.status = "disabled"
            identity.consent_withdrawn_at = stamp
            identity.updated_at = stamp
            identity.revision += 1
            for reference in uow.repo.identity_references(user_id, identity.id):
                if reference.local_path:
                    paths.append(Path(reference.local_path))
                reference.local_path = None
                reference.filename = None
                reference.review_status = "deleted"
                reference.deleted_at = stamp
                reference.is_primary = 0
            for validation in uow.repo.identity_validations(user_id, persona_id, 1000):
                if validation.status in {"queued", "running"} and validation.job_id:
                    job_ids.append(validation.job_id)
            uow.repo.add_identity_event(
                identity,
                "consent_withdrawn",
                detail={"references_deleted": len(paths)},
            )
            response = self._profile_response(uow.repo, identity)
        for job_id in job_ids:
            self.jobs.cancel(user_id, job_id)
        self._unlink(paths)
        return response

    def add_reference(
        self,
        user_id: str,
        persona_id: str,
        *,
        content: bytes,
        provenance: str,
        attested: bool,
        source_media_id: str | None = None,
    ) -> dict:
        if not attested:
            raise RequestError("The right to use this identity reference must be attested.", 400)
        if provenance not in {"user_upload", "generated_approved", "imported"}:
            raise RequestError("Unsupported reference provenance.", 400)
        normalized = normalize_identity_image(content, enforce_upload_limit=True)
        filename = f"{user_id}_{secrets.token_hex(12)}.jpg"
        target = self.config.identity_reference_dir / filename
        write_artifact_atomic(target, normalized.content)
        try:
            with self._uow() as uow:
                identity = self._identity(uow.repo, user_id, persona_id, create=False)
                if not identity or identity.consent_status != "granted":
                    raise ConflictError("Grant visual identity consent before adding references.")
                if source_media_id:
                    media = uow.repo.media(user_id, source_media_id)
                    if not media or media.kind != "image":
                        raise NotFoundError("source image not found")
                row = uow.repo.add_identity_reference(
                    user_id=user_id,
                    identity_id=identity.id,
                    persona_id=persona_id,
                    source_media_id=source_media_id,
                    filename=filename,
                    local_path=str(target),
                    content_type="image/jpeg",
                    byte_size=len(normalized.content),
                    width=normalized.width,
                    height=normalized.height,
                    sha256=normalized.digest,
                    provenance=provenance,
                    review_status="pending",
                    is_primary=0,
                    consent_attested_at=now_ts(),
                    created_at=now_ts(),
                )
                uow.repo.add_identity_event(
                    identity, "reference_added", reference_id=row.id, detail={"provenance": provenance}
                )
                return self._reference_response(row)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def add_reference_from_media(
        self,
        user_id: str,
        persona_id: str,
        media_id: str,
        *,
        attested: bool,
    ) -> dict:
        with self._uow() as uow:
            media = uow.repo.media(user_id, media_id)
            if not media or media.kind != "image":
                raise NotFoundError("source image not found")
            path = Path(media.local_path)
        if not path.exists() or not path.is_file():
            raise NotFoundError("source image file not found")
        return self.add_reference(
            user_id,
            persona_id,
            content=read_identity_image_file(path, max_bytes=MAX_REFERENCE_BYTES),
            provenance="generated_approved",
            attested=attested,
            source_media_id=media_id,
        )

    def review_reference(self, user_id: str, reference_id: str, *, approve: bool, reason: str = "") -> dict:
        with self._uow() as uow:
            row = uow.repo.identity_reference(user_id, reference_id)
            if not row or row.review_status == "deleted":
                raise NotFoundError("identity reference not found")
            identity = uow.repo.visual_identity_by_id(row.identity_id)
            if not identity or identity.user_id != user_id:
                raise NotFoundError("identity reference not found")
            if identity.consent_status != "granted":
                raise ConflictError("Visual identity consent is not active.")
            stamp = now_ts()
            row.review_status = "approved" if approve else "rejected"
            row.reviewed_at = stamp
            row.rejection_reason = None if approve else (reason.strip()[:500] or "Rejected by operator.")
            if approve:
                approved = uow.repo.approved_identity_references(user_id, identity.id)
                if not any(item.is_primary for item in approved):
                    row.is_primary = 1
                identity.status = "active"
            else:
                was_primary = bool(row.is_primary)
                row.is_primary = 0
                remaining = uow.repo.approved_identity_references(user_id, identity.id)
                if was_primary and remaining:
                    remaining[0].is_primary = 1
                if not remaining:
                    identity.status = "draft"
            identity.updated_at = stamp
            identity.revision += 1
            uow.repo.add_identity_event(
                identity,
                "reference_approved" if approve else "reference_rejected",
                reference_id=row.id,
                detail={"reason": row.rejection_reason} if not approve else {},
            )
            return self._reference_response(row)

    def delete_reference(self, user_id: str, reference_id: str) -> None:
        path = None
        with self._uow() as uow:
            row = uow.repo.identity_reference(user_id, reference_id)
            if not row or row.review_status == "deleted":
                raise NotFoundError("identity reference not found")
            identity = uow.repo.visual_identity_by_id(row.identity_id)
            path = Path(row.local_path) if row.local_path else None
            stamp = now_ts()
            was_primary = bool(row.is_primary)
            row.local_path = None
            row.filename = None
            row.review_status = "deleted"
            row.deleted_at = stamp
            row.is_primary = 0
            if was_primary:
                remaining = uow.repo.approved_identity_references(user_id, identity.id)
                if remaining:
                    remaining[0].is_primary = 1
            if not uow.repo.approved_identity_references(user_id, identity.id):
                identity.status = "draft" if identity.consent_status == "granted" else "disabled"
            identity.updated_at = stamp
            identity.revision += 1
            uow.repo.add_identity_event(identity, "reference_deleted", reference_id=row.id)
        self._unlink([path] if path else [])

    def reference_path(self, user_id: str, reference_id: str) -> Path:
        with self._uow() as uow:
            row = uow.repo.identity_reference(user_id, reference_id)
            if not row or row.review_status == "deleted" or not row.local_path:
                raise NotFoundError()
            path = Path(row.local_path)
        root = self.config.identity_reference_dir.resolve()
        resolved = path.resolve()
        if resolved.parent != root or not resolved.is_file():
            raise NotFoundError()
        return resolved

    def validate_generated_media(
        self,
        user_id: str,
        media_id: str,
        snapshot: dict,
        cancellation: CancellationToken,
    ) -> dict:
        """Validate one generated candidate inline without creating a second queue job."""
        try:
            provider_settings = self._provider_settings(user_id)
        except ConflictError:
            return {"status": "unavailable", "claim_status": "unverified", "validation": None}
        provider_name = provider_settings["provider"]
        provider = self.providers.get(provider_name) if provider_name != "disabled" else None
        if not provider:
            return {"status": "unavailable", "claim_status": "unverified", "validation": None}
        with self._uow() as uow:
            identity = uow.repo.visual_identity_by_id(snapshot.get("profile_id"))
            reference = uow.repo.identity_reference(user_id, snapshot.get("reference_id"))
            media = uow.repo.media(user_id, media_id)
            if (
                not identity
                or identity.user_id != user_id
                or identity.persona_id != snapshot.get("persona_id")
                or identity.revision != snapshot.get("profile_revision")
                or identity.status != "active"
                or identity.consent_status != "granted"
                or not reference
                or reference.identity_id != identity.id
                or reference.review_status != "approved"
                or reference.sha256 != snapshot.get("reference_sha256")
                or not reference.local_path
                or not media
                or media.kind != "image"
            ):
                return {"status": "unavailable", "claim_status": "unverified", "validation": None}
            validation = uow.repo.add_identity_validation(
                user_id=user_id,
                identity_id=identity.id,
                persona_id=identity.persona_id,
                candidate_media_id=media.id,
                job_id=None,
                provider=provider_name,
                status="running",
                failure_policy=str(snapshot.get("failure_policy") or identity.failure_policy),
                threshold=float(snapshot.get("acceptance_threshold") or identity.acceptance_threshold),
                created_at=now_ts(),
            )
            validation.started_at = now_ts()
            uow.repo.add_identity_event(identity, "validation_started", validation_id=validation.id)
            validation_id = validation.id
            reference_path = Path(reference.local_path)
            candidate_path = Path(media.local_path)
        try:
            cancellation.raise_if_cancelled()
            reference_content = read_identity_image_file(reference_path, max_bytes=MAX_REFERENCE_BYTES)
            if sha256(reference_content).hexdigest() != snapshot.get("reference_sha256"):
                raise ProviderError(
                    provider=provider_name,
                    code="identity_reference_changed",
                    user_message="The approved identity reference changed before validation.",
                )
            candidate = normalize_identity_image(
                read_identity_image_file(candidate_path, max_bytes=MAX_CANDIDATE_BYTES),
                enforce_upload_limit=False,
            )
            result = provider.verify(
                IdentityVerificationRequest(
                    base_url=provider_settings["base_url"],
                    api_key=provider_settings["api_key"],
                    timeout_seconds=provider_settings["timeout_seconds"],
                    source_content=reference_content,
                    target_content=candidate.content,
                ),
                cancellation,
            )
            with self._uow() as uow:
                row = uow.repo.identity_validation_by_id(validation_id)
                row.status = "passed" if result.similarity >= row.threshold else "failed"
                row.score = result.similarity
                row.matched_reference_id = snapshot.get("reference_id")
                row.source_face_count = result.source_face_count
                row.target_face_count = result.target_face_count
                row.provider_version = result.provider_version
                row.request_id = result.request_id
                row.completed_at = now_ts()
                profile = uow.repo.visual_identity_by_id(row.identity_id)
                uow.repo.add_identity_event(
                    profile, f"validation_{row.status}", validation_id=row.id, detail={"score": row.score}
                )
                public = self._validation_response(row)
            return {"status": public["status"], "claim_status": public["claim_status"], "validation": public}
        except ProviderError as exc:
            status = "cancelled" if exc.code == "cancelled" else "error"
            with self._uow() as uow:
                row = uow.repo.identity_validation_by_id(validation_id)
                if row:
                    row.status = status
                    row.error_code = exc.code
                    row.error_message = redact_sensitive_text(exc.user_message)[:500]
                    row.completed_at = now_ts()
                    profile = uow.repo.visual_identity_by_id(row.identity_id)
                    uow.repo.add_identity_event(profile, f"validation_{status}", validation_id=row.id)
            if status == "cancelled":
                raise
            return {
                "status": "error",
                "claim_status": "unverified",
                "validation": self._validation_response(row) if row else None,
            }
        except Exception as exc:
            with self._uow() as uow:
                row = uow.repo.identity_validation_by_id(validation_id)
                if row:
                    row.status = "error"
                    row.error_code = "identity_validation_failed"
                    row.error_message = "Identity validation could not be completed."
                    row.completed_at = now_ts()
                    profile = uow.repo.visual_identity_by_id(row.identity_id)
                    uow.repo.add_identity_event(profile, "validation_error", validation_id=row.id)
                    public = self._validation_response(row)
                else:
                    public = None
            self.logger.warning("inline identity validation failed error=%s", exc.__class__.__name__)
            return {"status": "error", "claim_status": "unverified", "validation": public}

    def validate_media(self, user_id: str, persona_id: str, media_id: str) -> dict:
        provider_settings = self._provider_settings(user_id)
        if provider_settings["provider"] == "disabled":
            raise ConflictError("Visual identity validation is disabled.")
        provider = self.providers.get(provider_settings["provider"])
        if not provider:
            raise ConflictError("The configured visual identity provider is not installed.")
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=False)
            if not identity or identity.consent_status != "granted" or identity.status != "active":
                raise ConflictError("An active, consented visual identity profile is required.")
            references = uow.repo.approved_identity_references(user_id, identity.id)
            if not references:
                raise ConflictError("Approve at least one identity reference before validation.")
            media = uow.repo.media(user_id, media_id)
            if not media or media.kind != "image":
                raise NotFoundError("candidate image not found")
            job = uow.repo.add_job(
                user_id=user_id,
                chat_id=media.chat_id,
                turn_id=None,
                kind="identity_validation",
                progress="Queued for identity validation",
            )
            validation = uow.repo.add_identity_validation(
                user_id=user_id,
                identity_id=identity.id,
                persona_id=persona_id,
                candidate_media_id=media_id,
                job_id=job.id,
                provider=provider_settings["provider"],
                status="queued",
                failure_policy=identity.failure_policy,
                threshold=identity.acceptance_threshold,
                created_at=now_ts(),
            )
            uow.repo.add_identity_event(identity, "validation_queued", validation_id=validation.id)
            reference_paths = [(row.id, Path(row.local_path)) for row in references if row.local_path]
            candidate_path = Path(media.local_path)
            validation_id = validation.id
            response = {"validation": self._validation_response(validation), "job": job_response(job)}

        def execute(cancellation: CancellationToken):
            if not candidate_path.is_file():
                raise NotFoundError("candidate image file not found")
            candidate = normalize_identity_image(
                read_identity_image_file(candidate_path, max_bytes=MAX_CANDIDATE_BYTES),
                enforce_upload_limit=False,
            )
            best = None
            for reference_id, path in reference_paths:
                cancellation.raise_if_cancelled()
                if not path.is_file():
                    continue
                result = provider.verify(
                    IdentityVerificationRequest(
                        base_url=provider_settings["base_url"],
                        api_key=provider_settings["api_key"],
                        timeout_seconds=provider_settings["timeout_seconds"],
                        source_content=read_identity_image_file(path, max_bytes=MAX_REFERENCE_BYTES),
                        target_content=candidate.content,
                    ),
                    cancellation,
                )
                if best is None or result.similarity > best[1].similarity:
                    best = (reference_id, result)
            if best is None:
                raise ProviderError(
                    provider=provider_settings["provider"],
                    code="identity_references_unavailable",
                    user_message="Approved identity reference files are unavailable.",
                )
            reference_id, result = best
            return {
                "validation_id": validation_id,
                "status": "passed" if result.similarity >= identity.acceptance_threshold else "failed",
                "score": result.similarity,
                "threshold": identity.acceptance_threshold,
                "matched_reference_id": reference_id,
                "source_face_count": result.source_face_count,
                "target_face_count": result.target_face_count,
                "provider_version": result.provider_version,
                "request_id": result.request_id,
            }

        def on_start(repo):
            row = repo.identity_validation_by_id(validation_id)
            if row:
                row.status = "running"
                row.started_at = now_ts()

        def on_success(repo, result):
            row = repo.identity_validation_by_id(validation_id)
            if not row:
                return result
            row.status = result["status"]
            row.score = result["score"]
            row.matched_reference_id = result["matched_reference_id"]
            row.source_face_count = result["source_face_count"]
            row.target_face_count = result["target_face_count"]
            row.provider_version = result["provider_version"]
            row.request_id = result["request_id"]
            row.completed_at = now_ts()
            profile = repo.visual_identity_by_id(row.identity_id)
            repo.add_identity_event(
                profile, f"validation_{row.status}", validation_id=row.id, detail={"score": row.score}
            )
            return self._validation_response(row)

        def on_failure(repo, code, message):
            row = repo.identity_validation_by_id(validation_id)
            if row and row.status not in {"passed", "failed", "cancelled"}:
                row.status = "error"
                row.error_code = code
                row.error_message = redact_sensitive_text(message)[:500]
                row.completed_at = now_ts()
                profile = repo.visual_identity_by_id(row.identity_id)
                repo.add_identity_event(profile, "validation_error", validation_id=row.id, detail={"code": code})

        def on_cancel(repo):
            row = repo.identity_validation_by_id(validation_id)
            if row and row.status not in {"passed", "failed", "error", "cancelled"}:
                row.status = "cancelled"
                row.error_code = "cancelled"
                row.error_message = "Validation cancelled."
                row.completed_at = now_ts()
                profile = repo.visual_identity_by_id(row.identity_id)
                repo.add_identity_event(profile, "validation_cancelled", validation_id=row.id)

        try:
            self.jobs.submit(
                job_id=job.id,
                job_type="identity_validation",
                user_id=user_id,
                chat_id=media.chat_id,
                turn_id=None,
                latency_class="media",
                model_key="identity-verifier",
                execution=JobExecution(
                    execute=execute,
                    on_start=on_start,
                    on_success=on_success,
                    on_failure=on_failure,
                    on_cancel=on_cancel,
                ),
            )
        except Exception:
            self.jobs.fail_unsubmitted(job.id, "Identity validation could not be queued.", on_failure)
            raise
        return response

    def validations(self, user_id: str, persona_id: str, limit: int = 50) -> list[dict]:
        with self._uow() as uow:
            if not uow.repo.persona(user_id, persona_id):
                raise NotFoundError("persona not found")
            return [self._validation_response(row) for row in uow.repo.identity_validations(user_id, persona_id, limit)]

    def media_status(self, user_id: str, media_id: str) -> dict:
        with self._uow() as uow:
            media = uow.repo.media(user_id, media_id)
            if not media:
                raise NotFoundError()
            conditioning = None
            plan = None
            snapshot = {}
            if media.generation_plan_id:
                plan = uow.repo.media_execution_plan(user_id, media.generation_plan_id)
                if plan:
                    try:
                        snapshot = json.loads(plan.identity_conditioning_json or "{}")
                    except (TypeError, ValueError):
                        snapshot = {}
            validation = uow.repo.latest_media_identity_validation(user_id, media_id)
            if media.generation_plan_id and plan:
                conditioning = public_identity_conditioning(
                    snapshot,
                    applied=True,
                    verification_status=validation.status if validation else None,
                    claim_status=(self._validation_response(validation)["claim_status"] if validation else None),
                )
            if not validation:
                return {
                    "media_id": media_id,
                    "persona_id": conditioning.get("persona_id") if conditioning else None,
                    "claim_status": "unverified" if conditioning else "not_evaluated",
                    "conditioning": conditioning,
                    "validation": None,
                }
            claim_status = {
                "passed": "verified",
                "failed": "rejected" if validation.failure_policy == "block_claim" else "unverified",
                "queued": "unverified",
                "running": "unverified",
                "error": "unverified",
                "cancelled": "unverified",
            }[validation.status]
            return {
                "media_id": media_id,
                "persona_id": validation.persona_id,
                "claim_status": claim_status,
                "conditioning": conditioning,
                "validation": self._validation_response(validation),
            }

    def history(self, user_id: str, persona_id: str) -> list[dict]:
        with self._uow() as uow:
            identity = self._identity(uow.repo, user_id, persona_id, create=False)
            if not identity:
                return []
            return [
                {
                    "id": row.id,
                    "action": row.action,
                    "reference_id": row.reference_id,
                    "validation_id": row.validation_id,
                    "sequence_number": row.sequence_number,
                    "detail": json.loads(row.detail_json or "{}"),
                    "created_at": row.created_at,
                }
                for row in uow.repo.identity_events(user_id, identity.id)
            ]

    def prepare_persona_deletion(self, user_id: str, persona_id: str):
        paths: list[Path] = []
        job_ids: list[str] = []
        with self._uow() as uow:
            identity = uow.repo.visual_identity(user_id, persona_id)
            if not identity:
                return None
            paths = [
                Path(row.local_path) for row in uow.repo.identity_references(user_id, identity.id) if row.local_path
            ]
            job_ids = [
                row.job_id
                for row in uow.repo.identity_validations(user_id, persona_id, 1000)
                if row.job_id and row.status in {"queued", "running"}
            ]
        for job_id in job_ids:
            self.jobs.cancel(user_id, job_id)
        return lambda: self._unlink(paths)

    def _identity(self, repo, user_id: str, persona_id: str, *, create: bool):
        if not repo.persona(user_id, persona_id):
            raise NotFoundError("persona not found")
        identity = repo.visual_identity(user_id, persona_id)
        return identity or (repo.create_visual_identity(user_id, persona_id) if create else None)

    def _provider_settings(self, user_id: str) -> dict:
        with self._uow() as uow:
            row = uow.repo.identity_settings(user_id)
            if not row:
                return {"provider": "disabled", "base_url": "", "api_key": "", "timeout_seconds": 15.0}
            api_key = self.secret_store.decrypt(row.api_key_encrypted) if row.api_key_encrypted else ""
            if row.provider == "compreface" and (not row.base_url or not api_key):
                raise ConflictError("CompreFace settings are incomplete.")
            return {
                "provider": row.provider,
                "base_url": row.base_url or "",
                "api_key": api_key,
                "timeout_seconds": float(row.timeout_seconds),
            }

    def _settings_response(self, row) -> dict:
        if not row:
            return {"provider": "disabled", "base_url": "", "api_key": "", "timeout_seconds": 15.0}
        secret = self.secret_store.decrypt(row.api_key_encrypted) if row.api_key_encrypted else ""
        return {
            "provider": row.provider,
            "base_url": row.base_url or "",
            "api_key": _masked(secret),
            "timeout_seconds": float(row.timeout_seconds),
        }

    def _profile_response(self, repo, identity) -> dict:
        references = repo.identity_references(identity.user_id, identity.id)
        approved = sum(row.review_status == "approved" for row in references)
        generation_workflow_configured, verification_configured = self._configuration_readiness(repo, identity.user_id)
        return {
            "id": identity.id,
            "persona_id": identity.persona_id,
            "status": identity.status,
            "consent_status": identity.consent_status,
            "appearance_description": identity.appearance_description or "",
            "acceptance_threshold": identity.acceptance_threshold,
            "max_generation_attempts": identity.max_generation_attempts,
            "failure_policy": identity.failure_policy,
            "conditioning_fallback": identity.conditioning_fallback,
            "revision": identity.revision,
            "consent_granted_at": identity.consent_granted_at,
            "consent_withdrawn_at": identity.consent_withdrawn_at,
            "created_at": identity.created_at,
            "updated_at": identity.updated_at,
            "approved_reference_count": approved,
            "generation_workflow_configured": generation_workflow_configured,
            "verification_configured": verification_configured,
            "validation_ready": bool(
                identity.status == "active"
                and identity.consent_status == "granted"
                and approved
                and verification_configured
            ),
            "references": [self._reference_response(row) for row in references],
        }

    @staticmethod
    def _configuration_readiness(repo, user_id: str) -> tuple[bool, bool]:
        settings = repo.identity_settings(user_id)
        enabled_resources = repo.media_catalog_resources(user_id, enabled=True)
        enabled_comfy_model_ids = {
            row.id
            for row in enabled_resources
            if row.resource_type == "model"
            and row.kind == "image"
            and row.provider_key == "local-image"
            and row.backend == "comfyui"
        }
        compatibility = repo.media_compatibility_map(user_id)
        generation_workflow_configured = any(
            row.resource_type == "workflow"
            and row.kind == "image"
            and row.provider_key == "local-image"
            and row.backend == "comfyui"
            and "identity_control" in _json_list(row.features_json)
            and bool(_json_object(row.default_settings_json).get("identity_image_bindings"))
            and bool(compatibility.get(row.id, set()) & enabled_comfy_model_ids)
            for row in enabled_resources
        )
        verification_configured = bool(
            settings and settings.provider != "disabled" and settings.base_url and settings.api_key_encrypted
        )
        return generation_workflow_configured, verification_configured

    @staticmethod
    def _empty_profile(
        persona_id: str,
        *,
        generation_workflow_configured: bool = False,
        verification_configured: bool = False,
    ) -> dict:
        return {
            "id": None,
            "persona_id": persona_id,
            "status": "draft",
            "consent_status": "not_granted",
            "appearance_description": "",
            "acceptance_threshold": 0.78,
            "max_generation_attempts": 2,
            "failure_policy": "show_unverified",
            "conditioning_fallback": "allow_unconditioned",
            "revision": 0,
            "consent_granted_at": None,
            "consent_withdrawn_at": None,
            "created_at": None,
            "updated_at": None,
            "approved_reference_count": 0,
            "generation_workflow_configured": generation_workflow_configured,
            "verification_configured": verification_configured,
            "validation_ready": False,
            "references": [],
        }

    @staticmethod
    def _reference_response(row) -> dict:
        return {
            "id": row.id,
            "persona_id": row.persona_id,
            "source_media_id": row.source_media_id,
            "content_url": f"/api/v1/identity-references/{row.id}/content" if row.local_path else None,
            "content_type": row.content_type,
            "byte_size": row.byte_size,
            "width": row.width,
            "height": row.height,
            "sha256": row.sha256,
            "provenance": row.provenance,
            "review_status": row.review_status,
            "is_primary": bool(row.is_primary),
            "rejection_reason": row.rejection_reason,
            "created_at": row.created_at,
            "reviewed_at": row.reviewed_at,
            "deleted_at": row.deleted_at,
        }

    @staticmethod
    def _validation_response(row) -> dict:
        error = None
        if row.error_code or row.error_message:
            error = {"code": row.error_code or "failed", "message": row.error_message or "Validation failed."}
        claim_status = "verified" if row.status == "passed" else "unverified"
        if row.status == "failed" and row.failure_policy == "block_claim":
            claim_status = "rejected"
        return {
            "id": row.id,
            "persona_id": row.persona_id,
            "candidate_media_id": row.candidate_media_id,
            "sequence_number": row.sequence_number,
            "created_order": row.created_order,
            "job_id": row.job_id,
            "matched_reference_id": row.matched_reference_id,
            "provider": row.provider,
            "status": row.status,
            "claim_status": claim_status,
            "failure_policy": row.failure_policy,
            "score": row.score,
            "threshold": row.threshold,
            "source_face_count": row.source_face_count,
            "target_face_count": row.target_face_count,
            "provider_version": row.provider_version,
            "request_id": row.request_id,
            "error": error,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    def _unlink(self, paths: list[Path]) -> None:
        root = self.config.identity_reference_dir.resolve()
        for path in paths:
            try:
                resolved = path.resolve()
                if resolved.parent == root:
                    resolved.unlink(missing_ok=True)
            except OSError as exc:
                self.logger.warning("identity reference cleanup failed error=%s", exc.__class__.__name__)
