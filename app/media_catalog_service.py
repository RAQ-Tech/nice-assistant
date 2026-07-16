from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from app.identity_conditioning import (
    IDENTITY_CONDITIONING_MODE,
    IDENTITY_CONTROL_FEATURE,
    IDENTITY_UNCONDITIONED_MODE,
    public_identity_conditioning,
)
from app.identity_images import MAX_REFERENCE_BYTES, read_identity_image_file
from app.media_planner import PROVIDER_DEFAULT, build_media_plan
from app.repositories import UnitOfWork
from app.service_errors import ConflictError, NotFoundError, RequestError


RESOURCE_TYPES = {"model", "lora", "workflow"}
MEDIA_KINDS = {"image", "video"}
PROVIDER_BACKENDS = {
    "openai-image": ("image", "openai"),
    "local-image": ("image", None),
    "openai-video": ("video", "openai"),
}
LOCAL_BACKENDS = {"automatic1111", "comfyui"}
MEDIA_OPERATIONS = {"generate", "inpaint", "outpaint", "image_to_image"}
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
COMFY_NODE_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")
COMFY_INPUT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,99}$")


def _json(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def _wire_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _tags(values: Any, *, label: str) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise RequestError(f"{label} must be a list", 400)
    result = []
    for value in values:
        tag = str(value or "").strip().lower()
        if not TAG_PATTERN.fullmatch(tag):
            raise RequestError(f"{label} contains an invalid tag", 400)
        if tag not in result:
            result.append(tag)
    if len(result) > 64:
        raise RequestError(f"{label} contains too many tags", 400)
    return result


def _strings(values: Any, *, label: str, max_items: int = 32, max_length: int = 200) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise RequestError(f"{label} must be a list", 400)
    result = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text or len(text) > max_length:
            raise RequestError(f"{label} contains an invalid value", 400)
        if text not in result:
            result.append(text)
    if len(result) > max_items:
        raise RequestError(f"{label} contains too many values", 400)
    return result


class MediaCatalogService:
    """Owner-scoped media metadata plus a deterministic, explainable selector."""

    def __init__(self, session_factory, secret_store, providers, logger):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.providers = providers
        self.logger = logger

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def catalog(self, user_id: str) -> dict:
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            setting = uow.repo.media_catalog_setting(user_id)
            resources = uow.repo.media_catalog_resources(user_id)
            return {
                "settings": self._settings_response(setting),
                "resources": [self._resource_response(uow.repo, row) for row in resources],
                "vocabulary": self._vocabulary(resources),
            }

    def update_settings(self, user_id: str, values: dict) -> dict:
        with self._uow() as uow:
            if not uow.repo.user(user_id):
                raise NotFoundError()
            self._ensure_imported(uow.repo, user_id)
            row = uow.repo.save_media_catalog_setting(
                user_id,
                {
                    "vram_budget_mb": int(values["vram_budget_mb"]),
                    "max_loras": int(values["max_loras"]),
                },
            )
            return self._settings_response(row)

    def create_resource(self, user_id: str, values: dict) -> dict:
        normalized, compatible_ids = self._normalize_resource(values)
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            self._validate_compatibility(uow.repo, user_id, normalized, compatible_ids)
            self._ensure_unique_external(uow.repo, user_id, normalized)
            row = uow.repo.add_media_catalog_resource(user_id, normalized)
            uow.repo.replace_media_resource_compatibility(row.id, compatible_ids)
            return self._resource_response(uow.repo, row)

    def update_resource(self, user_id: str, resource_id: str, values: dict) -> dict:
        normalized, compatible_ids = self._normalize_resource(values)
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            row = uow.repo.media_catalog_resource(user_id, resource_id)
            if not row:
                raise NotFoundError("media catalog resource not found")
            self._validate_compatibility(uow.repo, user_id, normalized, compatible_ids, resource_id=resource_id)
            if normalized["resource_type"] == "model":
                for dependent in uow.repo.media_resources_compatible_with_model(user_id, resource_id):
                    if (
                        dependent.kind != normalized["kind"]
                        or dependent.provider_key != normalized["provider_key"]
                        or dependent.backend != normalized["backend"]
                    ):
                        raise ConflictError(
                            "this model cannot change media kind, provider, or backend while compatible add-ons reference it"
                        )
            self._ensure_unique_external(uow.repo, user_id, normalized, exclude_id=resource_id)
            row = uow.repo.save_media_catalog_resource(row, normalized)
            uow.repo.replace_media_resource_compatibility(row.id, compatible_ids)
            return self._resource_response(uow.repo, row)

    def delete_resource(self, user_id: str, resource_id: str) -> bool:
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            return uow.repo.delete_media_catalog_resource(user_id, resource_id)

    def resource(self, user_id: str, resource_id: str) -> dict | None:
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            row = uow.repo.media_catalog_resource(user_id, resource_id)
            return self._resource_response(uow.repo, row) if row else None

    def vocabulary(self, user_id: str) -> dict:
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            return self._vocabulary(uow.repo.media_catalog_resources(user_id, enabled=True))

    def has_ready_resource(self, user_id: str, kind: str) -> bool:
        preview = self.preview(
            user_id,
            {
                "kind": kind,
                "operation": "generate",
                "domains": [],
                "content_tags": [],
                "required_features": [],
            },
        )
        return preview["status"] == "ready"

    def has_ready_operation(self, user_id: str, kind: str, operation: str) -> bool:
        preview = self.preview(
            user_id,
            {
                "kind": kind,
                "operation": operation,
                "domains": [],
                "content_tags": [],
                "required_features": [],
            },
        )
        return preview["status"] == "ready"

    def seed_newly_enabled_defaults(
        self,
        repo,
        user_id: str,
        previous_preferences: dict,
        preferences: dict,
    ) -> None:
        """Bootstrap missing catalog kinds when an owner enables a provider.

        Existing catalog resources are never rewritten or recreated. This keeps
        the operator-owned catalog authoritative while making late provider
        enablement behave like the original migration-time import.
        """

        transitions = {
            "image": (
                str(previous_preferences.get("image_provider") or "disabled").lower(),
                str(preferences.get("image_provider") or "disabled").lower(),
            ),
            "video": (
                str(previous_preferences.get("video_provider") or "disabled").lower(),
                str(preferences.get("video_provider") or "disabled").lower(),
            ),
        }
        existing_kinds = {row.kind for row in repo.media_catalog_resources(user_id)}
        newly_enabled = {
            kind
            for kind, (previous, current) in transitions.items()
            if previous == "disabled" and current != "disabled" and kind not in existing_kinds
        }
        if not newly_enabled:
            return
        for values in self._legacy_resource_values(preferences):
            if values["kind"] in newly_enabled:
                repo.add_media_catalog_resource(user_id, values)
        repo.save_media_catalog_setting(user_id, {"legacy_imported": 1})

    def preview(self, user_id: str, requirements: dict) -> dict:
        normalized = self._normalize_requirements(requirements)
        with self._uow() as uow:
            self._ensure_imported(uow.repo, user_id)
            built = self._build_plan(uow.repo, user_id, normalized, persona_id=None)
            return self._plan_response_values(None, "coordinator", normalized, built)

    def create_coordinator_plan(
        self,
        repo,
        user_id: str,
        capability_request_id: str,
        requirements: dict,
        *,
        persona_id: str | None,
    ):
        normalized = self._normalize_requirements(requirements)
        self._ensure_imported(repo, user_id)
        built = self._build_plan(repo, user_id, normalized, persona_id=persona_id)
        return repo.add_media_execution_plan(
            user_id=user_id,
            capability_request_id=capability_request_id,
            values=self._plan_row_values("coordinator", normalized, built),
        )

    def replan_coordinator_plan(
        self,
        repo,
        user_id: str,
        capability_request_id: str,
        requirements: dict,
        *,
        persona_id: str | None,
    ):
        normalized = self._normalize_requirements(requirements)
        self._ensure_imported(repo, user_id)
        row = repo.media_execution_plan_for_capability(user_id, capability_request_id)
        if not row:
            raise NotFoundError("media execution plan not found")
        if row.status != "blocked":
            raise ConflictError("Only a blocked media plan can be replanned.")
        built = self._build_plan(repo, user_id, normalized, persona_id=persona_id)
        return repo.save_media_execution_plan(
            row,
            self._plan_row_values("coordinator", normalized, built),
        )

    def create_manual_plan(self, repo, user_id: str, capability_request_id: str, kind: str):
        requirements = {
            "kind": kind,
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
        }
        built = {
            "status": "ready",
            "selected_resources": [],
            "execution_options": {},
            "explanation": {
                "summary": "Manual generation uses the explicitly submitted provider settings and bypasses catalog selection.",
                "selected": [],
                "warnings": ["This request was not selected by the media coordinator."],
                "rejected": [],
            },
            "estimated_vram_mb": 0,
            "block_code": None,
            "block_message": None,
            "identity_conditioning": {},
        }
        return repo.add_media_execution_plan(
            user_id=user_id,
            capability_request_id=capability_request_id,
            values=self._plan_row_values("manual", requirements, built),
        )

    def create_edit_plan(self, repo, user_id: str, capability_request_id: str, requirements: dict):
        normalized = self._normalize_requirements(requirements)
        if normalized["kind"] != "image" or normalized["operation"] == "generate":
            raise RequestError("image editing requires an inpaint, outpaint, or image-to-image operation", 400)
        self._ensure_imported(repo, user_id)
        built = self._build_plan(repo, user_id, normalized, persona_id=None)
        return repo.add_media_execution_plan(
            user_id=user_id,
            capability_request_id=capability_request_id,
            values=self._plan_row_values("coordinator", normalized, built),
        )

    def plan_for_capability(self, repo, user_id: str, capability_request_id: str) -> dict | None:
        row = repo.media_execution_plan_for_capability(user_id, capability_request_id)
        return self._plan_response(row) if row else None

    def plan(self, user_id: str, plan_id: str) -> dict | None:
        with self._uow() as uow:
            row = uow.repo.media_execution_plan(user_id, plan_id)
            return self._plan_response(row) if row else None

    def attempts(self, user_id: str, plan_id: str) -> list[dict]:
        with self._uow() as uow:
            if not uow.repo.media_execution_plan(user_id, plan_id):
                raise NotFoundError("media execution plan not found")
            return [self._attempt_response(row) for row in uow.repo.media_generation_attempts(user_id, plan_id)]

    def execution_values(self, repo, user_id: str, capability_request_id: str) -> dict:
        row = repo.media_execution_plan_for_capability(user_id, capability_request_id)
        if not row:
            raise ConflictError("The capability has no durable media execution plan.")
        if row.source == "manual":
            return {}
        if row.status != "ready":
            raise ConflictError(row.block_message or "No compatible media resources are available.")
        selected = _json(row.selected_resources_json, [])
        if not isinstance(selected, list) or not selected:
            raise ConflictError("The media execution plan has no selected model.")
        for snapshot in selected:
            if not isinstance(snapshot, dict) or not snapshot.get("id"):
                raise ConflictError("The media execution plan is invalid.")
            current = repo.media_catalog_resource(user_id, snapshot["id"])
            if not current or not current.enabled or current.revision != snapshot.get("revision"):
                raise ConflictError(
                    "A selected media resource changed after this request was planned. Create a new request before approval."
                )
        options = _json(row.execution_options_json, {})
        if not isinstance(options, dict):
            raise ConflictError("The media execution plan options are invalid.")
        self._revalidate_identity(repo, user_id, row, options)
        return options

    def execution_spec(self, repo, user_id: str, capability_request_id: str) -> dict:
        row = repo.media_execution_plan_for_capability(user_id, capability_request_id)
        options = self.execution_values(repo, user_id, capability_request_id)
        if row:
            options["_media_plan_id"] = row.id
            requirements = _json(row.requirements_json, {})
            operation = str(requirements.get("operation") or "generate")
            options["_operation"] = operation
            if operation != "generate":
                capability = repo.capability_request(user_id, capability_request_id)
                if not capability:
                    raise ConflictError("The media capability request is unavailable.")
                arguments = _json(capability.arguments_json, {})
                self._bind_edit_inputs(repo, user_id, operation, arguments, options)
        return {
            "options": options,
            "estimated_vram_mb": max(0, int(row.estimated_vram_mb or 0)) if row else 0,
        }

    @staticmethod
    def _bind_edit_inputs(repo, user_id: str, operation: str, arguments: dict, options: dict) -> None:
        source = repo.media(user_id, str(arguments.get("source_media_id") or ""))
        if not source or source.kind != "image" or not source.local_path or not Path(source.local_path).is_file():
            raise ConflictError("The selected source image is unavailable.")
        source_bindings = options.get("source_image_bindings")
        if options.get("backend") != "comfyui" or not isinstance(source_bindings, list) or not source_bindings:
            raise ConflictError("The selected ComfyUI edit workflow has no source image binding.")
        source_content = Path(source.local_path).read_bytes()
        options["_source_media_id"] = source.id
        options["_source_image_path"] = source.local_path
        options["_source_image_sha256"] = sha256(source_content).hexdigest()
        if operation not in {"inpaint", "outpaint"}:
            return
        mask = repo.media(user_id, str(arguments.get("mask_media_id") or ""))
        if not mask or mask.kind != "image" or not mask.local_path or not Path(mask.local_path).is_file():
            raise ConflictError("Inpaint and outpaint operations require an available mask image.")
        mask_bindings = options.get("mask_image_bindings")
        if not isinstance(mask_bindings, list) or not mask_bindings:
            raise ConflictError("The selected ComfyUI workflow has no mask image binding.")
        mask_content = Path(mask.local_path).read_bytes()
        options["_mask_media_id"] = mask.id
        options["_mask_image_path"] = mask.local_path
        options["_mask_image_sha256"] = sha256(mask_content).hexdigest()

    def _build_plan(self, repo, user_id: str, requirements: dict, *, persona_id: str | None) -> dict:
        built = build_media_plan(repo, user_id, requirements, self.providers)
        identity_required = IDENTITY_CONTROL_FEATURE in requirements["required_features"]
        built["identity_conditioning"] = (
            {
                "required": True,
                "status": "blocked",
                "mode": IDENTITY_CONDITIONING_MODE,
                "persona_id": persona_id,
            }
            if identity_required
            else {}
        )
        if not identity_required:
            return built
        if built["status"] == "ready":
            bound = self._bind_identity(repo, user_id, persona_id, requirements, built)
            if bound["status"] == "ready":
                return bound
            return self._build_unconditioned_fallback(repo, user_id, persona_id, requirements, bound)
        if not self._identity_configuration_missing(built):
            return built
        return self._build_unconditioned_fallback(repo, user_id, persona_id, requirements, built)

    @staticmethod
    def _identity_configuration_missing(built: dict) -> bool:
        return any(
            "missing required features: identity_control" in reason
            for candidate in built.get("explanation", {}).get("rejected", [])
            for reason in candidate.get("reasons", [])
        )

    def _build_unconditioned_fallback(self, repo, user_id, persona_id, requirements, blocked):
        if not persona_id:
            return self._block_identity(
                blocked,
                "identity_persona_required",
                "Identity-aware generation requires a chat with a selected persona.",
            )
        identity = repo.visual_identity(user_id, persona_id)
        fallback_policy = identity.conditioning_fallback if identity else "allow_unconditioned"
        if fallback_policy != "allow_unconditioned":
            return blocked
        relaxed = dict(requirements)
        relaxed["required_features"] = [
            feature for feature in requirements["required_features"] if feature != IDENTITY_CONTROL_FEATURE
        ]
        fallback = build_media_plan(repo, user_id, relaxed, self.providers)
        appearance_description = (
            identity.appearance_description
            if identity and identity.status == "active" and identity.consent_status == "granted"
            else ""
        )
        fallback["identity_conditioning"] = {
            "required": True,
            "status": "unconditioned",
            "mode": IDENTITY_UNCONDITIONED_MODE,
            "persona_id": persona_id,
            "profile_id": identity.id if identity else None,
            "profile_revision": identity.revision if identity else None,
            "reference_id": None,
            "reference_sha256": None,
            "acceptance_threshold": float(identity.acceptance_threshold) if identity else None,
            "max_generation_attempts": int(identity.max_generation_attempts) if identity else None,
            "failure_policy": identity.failure_policy if identity else None,
            "conditioning_fallback": fallback_policy,
            "appearance_description": appearance_description or "",
            "fallback_reason": blocked.get("block_message"),
        }
        if fallback["status"] != "ready":
            fallback["explanation"]["warnings"].append(
                "Identity matching was allowed to fall back, but the ordinary media plan is still unavailable."
            )
            return fallback
        fallback["explanation"]["summary"] = (
            "Persona identity conditioning is unavailable for this request, so the explicit policy selected ordinary "
            "generation without reference conditioning."
        )
        if blocked.get("block_message"):
            fallback["explanation"]["warnings"].append(
                f"Identity conditioning was unavailable: {blocked['block_message']}"
            )
        fallback["explanation"]["warnings"].append(
            "No persona identity reference will be applied. Appearance guidance may be included, but the "
            "result is unconditioned and explicitly unverified."
        )
        return fallback

    def _bind_identity(self, repo, user_id, persona_id, requirements, built):
        if requirements["kind"] != "image":
            return self._block_identity(
                built,
                "identity_operation_unavailable",
                "Persona identity conditioning is available only for image workflows.",
                persona_id=persona_id,
            )
        workflow = next(
            (
                item
                for item in built["selected_resources"]
                if item.get("resource_type") == "workflow" and IDENTITY_CONTROL_FEATURE in item.get("features", [])
            ),
            None,
        )
        bindings = ((workflow or {}).get("default_settings") or {}).get("identity_image_bindings")
        if not workflow or not isinstance(bindings, list) or not bindings:
            return self._block_identity(
                built,
                "identity_workflow_unavailable",
                "The selected plan does not contain a ComfyUI identity workflow with explicit image bindings.",
                persona_id=persona_id,
            )
        if not persona_id:
            return self._block_identity(
                built,
                "identity_persona_required",
                "Identity-aware generation requires a chat with a selected persona.",
            )
        identity = repo.visual_identity(user_id, persona_id)
        if not identity or identity.status != "active" or identity.consent_status != "granted":
            return self._block_identity(
                built,
                "identity_profile_unavailable",
                "The selected persona needs an active, consented visual identity profile.",
                persona_id=persona_id,
                profile=identity,
            )
        references = repo.approved_identity_references(user_id, identity.id)
        reference = references[0] if references else None
        if not reference or not reference.local_path:
            return self._block_identity(
                built,
                "identity_reference_unavailable",
                "The selected persona needs an approved identity reference.",
                persona_id=persona_id,
                profile=identity,
            )
        try:
            content = read_identity_image_file(Path(reference.local_path), max_bytes=MAX_REFERENCE_BYTES)
        except RequestError:
            return self._block_identity(
                built,
                "identity_reference_unavailable",
                "The approved identity reference file is unavailable.",
                persona_id=persona_id,
                profile=identity,
            )
        if sha256(content).hexdigest() != reference.sha256:
            return self._block_identity(
                built,
                "identity_reference_changed",
                "The approved identity reference no longer matches its reviewed content.",
                persona_id=persona_id,
                profile=identity,
            )
        built["identity_conditioning"] = {
            "required": True,
            "status": "ready",
            "mode": IDENTITY_CONDITIONING_MODE,
            "persona_id": persona_id,
            "profile_id": identity.id,
            "profile_revision": identity.revision,
            "reference_id": reference.id,
            "reference_sha256": reference.sha256,
            "workflow_resource_id": workflow["id"],
            "acceptance_threshold": float(identity.acceptance_threshold),
            "max_generation_attempts": int(identity.max_generation_attempts),
            "failure_policy": identity.failure_policy,
            "conditioning_fallback": identity.conditioning_fallback,
            "appearance_description": identity.appearance_description or "",
            "identity_image_bindings": bindings,
        }
        correction = self._identity_correction_workflow(repo, user_id, built, workflow["id"])
        if correction:
            correction_settings = correction["default_settings"]
            non_workflow_vram = sum(
                item["estimated_vram_mb"] for item in built["selected_resources"] if item["resource_type"] != "workflow"
            )
            correction_stage_vram = non_workflow_vram + correction["estimated_vram_mb"]
            setting = repo.media_catalog_setting(user_id)
            if setting.vram_budget_mb and correction_stage_vram > setting.vram_budget_mb:
                built["explanation"]["warnings"].append(
                    "The compatible correction workflow exceeds the catalog VRAM budget; failed validation will rerun the original workflow."
                )
                correction = None
        if correction:
            correction_settings = correction["default_settings"]
            built["identity_conditioning"]["correction_workflow_resource_id"] = correction["id"]
            built["identity_conditioning"]["correction_workflow_revision"] = correction["revision"]
            built["identity_conditioning"]["correction_workflow_patch"] = correction_settings["workflow_patch"]
            built["identity_conditioning"]["correction_source_image_bindings"] = correction_settings[
                "source_image_bindings"
            ]
            built["identity_conditioning"]["correction_identity_image_bindings"] = correction_settings[
                "identity_image_bindings"
            ]
            built["selected_resources"].append(correction)
            built["estimated_vram_mb"] = max(built["estimated_vram_mb"], correction_stage_vram)
            built["explanation"]["selected"].append(
                {
                    "resource_id": correction["id"],
                    "role": "correction_workflow",
                    "name": correction["name"],
                    "reason": "eligible image-to-image correction stage for failed identity validation",
                }
            )
        built["explanation"]["warnings"].append(
            "The output will be conditioned on the approved persona reference but remains unverified until a comparison passes."
        )
        return built

    @staticmethod
    def _identity_correction_workflow(repo, user_id: str, built: dict, primary_workflow_id: str) -> dict | None:
        model = next((item for item in built["selected_resources"] if item["resource_type"] == "model"), None)
        if not model or model.get("backend") != "comfyui":
            return None
        compatibility = repo.media_compatibility_map(user_id)
        candidates = []
        for row in repo.media_catalog_resources(user_id, enabled=True):
            if row.id == primary_workflow_id or row.resource_type != "workflow" or row.kind != "image":
                continue
            if row.provider_key != model["provider_key"] or row.backend != "comfyui":
                continue
            if model["id"] not in compatibility.get(row.id, set()):
                continue
            operations = set(_json(row.operations_json, []))
            features = set(_json(row.features_json, []))
            settings = _json(row.default_settings_json, {})
            if (
                "image_to_image" not in operations
                or IDENTITY_CONTROL_FEATURE not in features
                or not settings.get("source_image_bindings")
                or not settings.get("identity_image_bindings")
            ):
                continue
            candidates.append(row)
        candidates.sort(key=lambda row: (-row.priority, row.estimated_vram_mb, row.name.casefold(), row.id))
        if not candidates:
            return None
        row = candidates[0]
        return {
            "id": row.id,
            "resource_type": row.resource_type,
            "name": row.name,
            "provider_key": row.provider_key,
            "backend": row.backend,
            "external_id": row.external_id,
            "domains": _json(row.domains_json, []),
            "content_tags": _json(row.content_tags_json, []),
            "features": _json(row.features_json, []),
            "estimated_vram_mb": row.estimated_vram_mb,
            "default_settings": _json(row.default_settings_json, {}),
            "updated_at": row.updated_at,
            "revision": row.revision,
        }

    @staticmethod
    def _block_identity(built, code, message, *, persona_id=None, profile=None):
        built["status"] = "blocked"
        built["block_code"] = code
        built["block_message"] = message
        built["identity_conditioning"] = {
            "required": True,
            "status": "blocked",
            "mode": IDENTITY_CONDITIONING_MODE,
            "persona_id": persona_id,
            "profile_id": profile.id if profile else None,
            "profile_revision": profile.revision if profile else None,
        }
        built["explanation"]["warnings"].append(message)
        return built

    @staticmethod
    def _revalidate_identity(repo, user_id, row, options):
        requirements = _json(row.requirements_json, {})
        required = IDENTITY_CONTROL_FEATURE in (requirements.get("required_features") or [])
        snapshot = _json(row.identity_conditioning_json, {})
        if not required:
            return
        if not isinstance(snapshot, dict) or snapshot.get("status") not in {"ready", "unconditioned"}:
            raise ConflictError("The identity-aware plan has no executable identity binding.")
        identity = repo.visual_identity(user_id, snapshot.get("persona_id"))
        if snapshot.get("status") == "unconditioned":
            if snapshot.get("conditioning_fallback") != "allow_unconditioned":
                raise ConflictError(
                    "The persona identity fallback policy changed after this request was planned. Create a new request."
                )
            profile_id = snapshot.get("profile_id")
            if profile_id:
                if (
                    not identity
                    or identity.id != profile_id
                    or identity.revision != snapshot.get("profile_revision")
                    or identity.conditioning_fallback != "allow_unconditioned"
                ):
                    raise ConflictError(
                        "The persona identity profile changed after this request was planned. Create a new request before approval."
                    )
            elif identity:
                raise ConflictError(
                    "The persona identity profile changed after this request was planned. Create a new request before approval."
                )
            options["_identity_conditioning"] = snapshot
            return
        if (
            not identity
            or identity.id != snapshot.get("profile_id")
            or identity.revision != snapshot.get("profile_revision")
            or identity.status != "active"
            or identity.consent_status != "granted"
        ):
            raise ConflictError(
                "The persona identity profile changed after this request was planned. Create a new request before approval."
            )
        reference = repo.identity_reference(user_id, snapshot.get("reference_id"))
        if (
            not reference
            or reference.identity_id != identity.id
            or reference.persona_id != identity.persona_id
            or reference.review_status != "approved"
            or reference.sha256 != snapshot.get("reference_sha256")
            or not reference.local_path
        ):
            raise ConflictError(
                "The approved identity reference changed after this request was planned. Create a new request before approval."
            )
        try:
            content = read_identity_image_file(Path(reference.local_path), max_bytes=MAX_REFERENCE_BYTES)
        except RequestError as exc:
            raise ConflictError("The approved identity reference file is unavailable.") from exc
        if sha256(content).hexdigest() != reference.sha256:
            raise ConflictError("The approved identity reference no longer matches its reviewed content.")
        bindings = snapshot.get("identity_image_bindings")
        if options.get("backend") != "comfyui" or not isinstance(bindings, list) or not bindings:
            raise ConflictError("The selected adapter cannot execute the identity-aware workflow.")
        options["identity_image_bindings"] = bindings
        options["_identity_reference_path"] = str(Path(reference.local_path))
        options["_identity_reference_sha256"] = reference.sha256
        options["_identity_conditioning"] = snapshot
        correction_id = snapshot.get("correction_workflow_resource_id")
        if correction_id:
            correction = repo.media_catalog_resource(user_id, correction_id)
            if (
                not correction
                or not correction.enabled
                or correction.revision != snapshot.get("correction_workflow_revision")
            ):
                raise ConflictError(
                    "The identity correction workflow changed after this request was planned. Create a new request."
                )

    def _ensure_imported(self, repo, user_id: str) -> None:
        setting = repo.media_catalog_setting(user_id)
        if setting.legacy_imported:
            return
        settings = repo.settings(user_id) or {"preferences": {}}
        preferences = settings.get("preferences") or {}
        for values in self._legacy_resource_values(preferences):
            repo.add_media_catalog_resource(user_id, values)
        repo.save_media_catalog_setting(user_id, {"legacy_imported": 1})

    @staticmethod
    def _legacy_resource_values(preferences: dict) -> list[dict]:
        values = []
        image_provider = str(preferences.get("image_provider") or "disabled").lower()
        if image_provider != "disabled":
            if image_provider == "openai":
                values.append(
                    MediaCatalogService._resource_row_values(
                        {
                            "resource_type": "model",
                            "kind": "image",
                            "name": "Imported OpenAI image default",
                            "provider_key": "openai-image",
                            "backend": "openai",
                            "external_id": PROVIDER_DEFAULT,
                            "enabled": True,
                            "priority": 50,
                            "operations": ["generate"],
                            "domains": [],
                            "content_tags": ["general"],
                            "features": ["text_to_image"],
                            "estimated_vram_mb": 0,
                            "estimated_load_seconds": 0,
                            "default_settings": {
                                "size": preferences.get("image_size") or "1024x1024",
                                "quality": preferences.get("image_quality") or "auto",
                            },
                            "notes": "Imported from the pre-catalog image settings.",
                        }
                    )
                )
            else:
                backend = str(preferences.get("image_local_backend") or "automatic1111").lower()
                if backend not in LOCAL_BACKENDS:
                    backend = "automatic1111"
                allow_nsfw = bool(preferences.get("image_local_allow_nsfw", False))
                values.append(
                    MediaCatalogService._resource_row_values(
                        {
                            "resource_type": "model",
                            "kind": "image",
                            "name": f"Imported {backend} image model",
                            "provider_key": "local-image",
                            "backend": backend,
                            "external_id": str(preferences.get("image_local_model") or PROVIDER_DEFAULT),
                            "enabled": True,
                            "priority": 50,
                            "operations": ["generate"],
                            "domains": [],
                            "content_tags": ["general", "adult", "nudity", "explicit"] if allow_nsfw else ["general"],
                            "features": ["text_to_image"],
                            "estimated_vram_mb": 0,
                            "estimated_load_seconds": 0,
                            "default_settings": {
                                "size": preferences.get("image_size") or "1024x1024",
                                "quality": preferences.get("image_quality") or "auto",
                                "steps": preferences.get("image_local_steps"),
                                "cfg_scale": preferences.get("image_local_cfg_scale"),
                                "sampler_name": preferences.get("image_local_sampler_name"),
                                "scheduler": preferences.get("image_local_scheduler"),
                                "allow_nsfw": allow_nsfw,
                            },
                            "notes": "Imported from the pre-catalog image settings.",
                        }
                    )
                )
        if str(preferences.get("video_provider") or "disabled").lower() == "openai":
            values.append(
                MediaCatalogService._resource_row_values(
                    {
                        "resource_type": "model",
                        "kind": "video",
                        "name": "Imported OpenAI video model",
                        "provider_key": "openai-video",
                        "backend": "openai",
                        "external_id": str(preferences.get("video_model") or "sora-2"),
                        "enabled": True,
                        "priority": 50,
                        "operations": ["generate"],
                        "domains": [],
                        "content_tags": ["general"],
                        "features": ["text_to_video"],
                        "estimated_vram_mb": 0,
                        "estimated_load_seconds": 0,
                        "default_settings": {
                            "size": preferences.get("video_size") or "720x1280",
                            "seconds": preferences.get("video_duration") or "4",
                        },
                        "notes": "Imported from the pre-catalog video settings.",
                    }
                )
            )
        return values

    def _normalize_resource(self, values: dict) -> tuple[dict, list[str]]:
        resource_type = str(values.get("resource_type") or "").strip().lower()
        kind = str(values.get("kind") or "").strip().lower()
        provider_key = str(values.get("provider_key") or "").strip().lower()
        backend = str(values.get("backend") or "").strip().lower()
        name = " ".join(str(values.get("name") or "").split()).strip()
        external_id = str(values.get("external_id") or "").strip()
        if resource_type not in RESOURCE_TYPES or kind not in MEDIA_KINDS:
            raise RequestError("invalid media catalog resource type or kind", 400)
        expected = PROVIDER_BACKENDS.get(provider_key)
        if not expected or expected[0] != kind:
            raise RequestError("media provider does not support this media kind", 400)
        if expected[1] and backend != expected[1]:
            raise RequestError("media provider and backend are incompatible", 400)
        if provider_key == "local-image" and backend not in LOCAL_BACKENDS:
            raise RequestError("local image resources require Automatic1111 or ComfyUI", 400)
        if resource_type != "model" and provider_key != "local-image":
            raise RequestError("LoRA and workflow resources are supported only by local image providers", 400)
        if resource_type == "workflow" and backend != "comfyui":
            raise RequestError("workflow resources currently require ComfyUI", 400)
        if provider_key == "openai-image" and external_id != PROVIDER_DEFAULT:
            raise RequestError("the OpenAI image adapter currently supports only its provider-default model", 400)
        if provider_key == "openai-video" and external_id not in {"sora-2", "sora-2-pro"}:
            raise RequestError("unsupported OpenAI video model", 400)
        if not name or len(name) > 160 or not external_id or len(external_id) > 500:
            raise RequestError("media resource name or external ID is invalid", 400)
        operations = _tags(values.get("operations") or [], label="operations")
        if not operations or not set(operations) <= MEDIA_OPERATIONS:
            raise RequestError("media resource operations are invalid", 400)
        enabled = bool(values.get("enabled", True))
        features = _tags(values.get("features") or [], label="features")
        default_settings = self._normalize_default_settings(
            resource_type,
            provider_key,
            backend,
            values.get("default_settings") or {},
            allow_empty_workflow=resource_type == "workflow" and not enabled,
        )
        if resource_type == "workflow":
            bindings = default_settings.get("identity_image_bindings") or []
            source_bindings = default_settings.get("source_image_bindings") or []
            mask_bindings = default_settings.get("mask_image_bindings") or []
            declares_identity = IDENTITY_CONTROL_FEATURE in features
            if enabled and declares_identity and not bindings:
                raise RequestError(
                    "identity_control workflows require at least one explicit identity image binding",
                    400,
                )
            if bindings and not declares_identity:
                raise RequestError(
                    "identity image bindings require the identity_control feature",
                    400,
                )
            if enabled and set(operations) & {"image_to_image", "inpaint", "outpaint"} and not source_bindings:
                raise RequestError("enabled editing workflows require at least one source image binding", 400)
            if enabled and set(operations) & {"inpaint", "outpaint"} and not mask_bindings:
                raise RequestError(
                    "enabled inpaint and outpaint workflows require at least one mask image binding", 400
                )
        normalized = {
            "resource_type": resource_type,
            "kind": kind,
            "name": name,
            "provider_key": provider_key,
            "backend": backend,
            "external_id": external_id,
            "enabled": int(enabled),
            "priority": int(values.get("priority", 50)),
            "operations_json": _wire_json(operations),
            "domains_json": _wire_json(_tags(values.get("domains") or [], label="domains")),
            "content_tags_json": _wire_json(_tags(values.get("content_tags") or [], label="content tags")),
            "features_json": _wire_json(features),
            "estimated_vram_mb": int(values.get("estimated_vram_mb", 0)),
            "estimated_load_seconds": float(values.get("estimated_load_seconds", 0)),
            "default_settings_json": _wire_json(default_settings),
            "notes": str(values.get("notes") or "").strip()[:4000] or None,
        }
        compatible_ids = [str(value) for value in values.get("compatible_model_ids") or [] if value]
        return normalized, list(dict.fromkeys(compatible_ids))

    @staticmethod
    def _normalize_default_settings(
        resource_type: str,
        provider_key: str,
        backend: str,
        values: Any,
        *,
        allow_empty_workflow: bool = False,
    ) -> dict:
        if not isinstance(values, dict):
            raise RequestError("default settings must be an object", 400)
        if resource_type == "model" and provider_key == "openai-image":
            allowed = {"size", "quality"}
        elif resource_type == "model" and provider_key == "openai-video":
            allowed = {"size", "seconds"}
        elif resource_type == "model":
            allowed = {"size", "quality", "steps", "cfg_scale", "sampler_name", "scheduler", "allow_nsfw"}
        elif resource_type == "lora":
            allowed = {"weight", "trigger_words"}
        else:
            allowed = {
                "workflow_patch",
                "identity_image_bindings",
                "source_image_bindings",
                "mask_image_bindings",
            }
        if set(values) - allowed:
            raise RequestError("default settings include unsupported fields", 400)
        result = dict(values)
        if resource_type == "lora":
            weight = float(result.get("weight", 1.0))
            if not 0 <= weight <= 4:
                raise RequestError("LoRA weight must be between 0 and 4", 400)
            result["weight"] = weight
            result["trigger_words"] = _strings(result.get("trigger_words") or [], label="trigger words")
        if resource_type == "workflow":
            patch = result.get("workflow_patch") or {}
            if not isinstance(patch, dict) or len(_wire_json(patch).encode("utf-8")) > 200_000:
                raise RequestError("workflow patch must be a JSON object no larger than 200 KB", 400)
            if not patch and not allow_empty_workflow:
                raise RequestError("workflow resources require a non-empty inline workflow patch", 400)
            result["workflow_patch"] = patch
            result["identity_image_bindings"] = MediaCatalogService._normalize_identity_bindings(
                patch,
                result.get("identity_image_bindings") or [],
            )
            result["source_image_bindings"] = MediaCatalogService._normalize_comfy_bindings(
                patch, result.get("source_image_bindings") or [], "source image"
            )
            result["mask_image_bindings"] = MediaCatalogService._normalize_comfy_bindings(
                patch, result.get("mask_image_bindings") or [], "mask image"
            )
        if resource_type == "model":
            result = {key: value for key, value in result.items() if value not in (None, "")}
            try:
                if "steps" in result:
                    result["steps"] = int(result["steps"])
                    if not 1 <= result["steps"] <= 500:
                        raise ValueError
                if "cfg_scale" in result:
                    result["cfg_scale"] = float(result["cfg_scale"])
                    if not 0 <= result["cfg_scale"] <= 50:
                        raise ValueError
            except (TypeError, ValueError) as exc:
                raise RequestError("model numeric defaults are invalid", 400) from exc
            if "allow_nsfw" in result and not isinstance(result["allow_nsfw"], bool):
                raise RequestError("allow_nsfw must be true or false", 400)
            for key in ("size", "quality", "sampler_name", "scheduler", "seconds"):
                if key in result:
                    result[key] = str(result[key]).strip()
                    if not result[key] or len(result[key]) > 200:
                        raise RequestError(f"model {key} is invalid", 400)
        return result

    @staticmethod
    def _normalize_identity_bindings(workflow_patch: dict, values: Any) -> list[dict]:
        return MediaCatalogService._normalize_comfy_bindings(workflow_patch, values, "identity image")

    @staticmethod
    def _normalize_comfy_bindings(workflow_patch: dict, values: Any, label: str) -> list[dict]:
        if not isinstance(values, list) or len(values) > 8:
            raise RequestError(f"{label} bindings must be a list with at most eight entries", 400)
        result = []
        for value in values:
            if not isinstance(value, dict) or set(value) != {"node_id", "input_name"}:
                raise RequestError(f"{label} bindings require node_id and input_name", 400)
            node_id = str(value.get("node_id") or "").strip()
            input_name = str(value.get("input_name") or "").strip()
            if not COMFY_NODE_ID_PATTERN.fullmatch(node_id) or not COMFY_INPUT_NAME_PATTERN.fullmatch(input_name):
                raise RequestError(f"{label} binding contains an invalid node or input name", 400)
            node = workflow_patch.get(node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict) or input_name not in inputs:
                raise RequestError(f"{label} binding must target an input in the inline workflow patch", 400)
            binding = {"node_id": node_id, "input_name": input_name}
            if binding not in result:
                result.append(binding)
        return result

    @staticmethod
    def _attempt_response(row) -> dict:
        error = None
        if row.error_code or row.error_message:
            error = {"code": row.error_code or "failed", "message": row.error_message or "Attempt failed."}
        return {
            "id": row.id,
            "media_plan_id": row.media_plan_id,
            "attempt_number": row.attempt_number,
            "operation": row.operation,
            "status": row.status,
            "media_id": row.media_id,
            "media_url": f"/api/v1/media/{row.media_id}" if row.media_id else None,
            "validation_id": row.validation_id,
            "source_media_id": row.source_media_id,
            "workflow_resource_id": row.workflow_resource_id,
            "score": row.score,
            "threshold": row.threshold,
            "error": error,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    def _validate_compatibility(self, repo, user_id, values, compatible_ids, *, resource_id=None):
        if values["resource_type"] == "model":
            if compatible_ids:
                raise RequestError("model resources cannot declare compatible base models", 400)
            return
        if values["enabled"] and not compatible_ids:
            raise RequestError("enabled LoRA and workflow resources require compatible base models", 400)
        for model_id in compatible_ids:
            if model_id == resource_id:
                raise RequestError("a media resource cannot be compatible with itself", 400)
            model = repo.media_catalog_resource(user_id, model_id)
            if not model or model.resource_type != "model":
                raise RequestError("compatible model was not found", 400)
            if (
                model.kind != values["kind"]
                or model.provider_key != values["provider_key"]
                or model.backend != values["backend"]
            ):
                raise RequestError("compatible model uses a different media kind, provider, or backend", 400)

    @staticmethod
    def _ensure_unique_external(repo, user_id, values, *, exclude_id=None):
        for row in repo.media_catalog_resources(user_id):
            if row.id == exclude_id:
                continue
            if (
                row.resource_type == values["resource_type"]
                and row.provider_key == values["provider_key"]
                and row.backend == values["backend"]
                and row.external_id == values["external_id"]
            ):
                raise ConflictError("that external media resource is already cataloged")

    @staticmethod
    def _normalize_requirements(values: dict) -> dict:
        kind = str(values.get("kind") or "").strip().lower()
        operation = str(values.get("operation") or "generate").strip().lower()
        if kind not in MEDIA_KINDS or operation not in MEDIA_OPERATIONS:
            raise RequestError("invalid media planning requirements", 400)
        return {
            "kind": kind,
            "operation": operation,
            "domains": _tags(values.get("domains") or [], label="domains"),
            "content_tags": _tags(values.get("content_tags") or [], label="content tags"),
            "required_features": _tags(values.get("required_features") or [], label="required features"),
        }

    @staticmethod
    def _resource_row_values(values: dict) -> dict:
        return {
            "resource_type": values["resource_type"],
            "kind": values["kind"],
            "name": values["name"],
            "provider_key": values["provider_key"],
            "backend": values["backend"],
            "external_id": values["external_id"],
            "enabled": int(bool(values["enabled"])),
            "priority": int(values["priority"]),
            "operations_json": _wire_json(values["operations"]),
            "domains_json": _wire_json(values["domains"]),
            "content_tags_json": _wire_json(values["content_tags"]),
            "features_json": _wire_json(values["features"]),
            "estimated_vram_mb": int(values["estimated_vram_mb"]),
            "estimated_load_seconds": float(values["estimated_load_seconds"]),
            "default_settings_json": _wire_json(values["default_settings"]),
            "notes": values.get("notes"),
        }

    @staticmethod
    def _settings_response(row) -> dict:
        return {"vram_budget_mb": row.vram_budget_mb, "max_loras": row.max_loras}

    @staticmethod
    def _resource_response(repo, row) -> dict:
        return {
            "id": row.id,
            "resource_type": row.resource_type,
            "kind": row.kind,
            "name": row.name,
            "provider_key": row.provider_key,
            "backend": row.backend,
            "external_id": row.external_id,
            "enabled": bool(row.enabled),
            "priority": row.priority,
            "operations": _json(row.operations_json, []),
            "domains": _json(row.domains_json, []),
            "content_tags": _json(row.content_tags_json, []),
            "features": _json(row.features_json, []),
            "estimated_vram_mb": row.estimated_vram_mb,
            "estimated_load_seconds": row.estimated_load_seconds,
            "default_settings": _json(row.default_settings_json, {}),
            "notes": row.notes or "",
            "compatible_model_ids": repo.media_resource_compatible_model_ids(row.id),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "revision": row.revision,
        }

    @staticmethod
    def _snapshot(row) -> dict:
        return {
            "id": row.id,
            "resource_type": row.resource_type,
            "name": row.name,
            "provider_key": row.provider_key,
            "backend": row.backend,
            "external_id": row.external_id,
            "domains": _json(row.domains_json, []),
            "content_tags": _json(row.content_tags_json, []),
            "features": _json(row.features_json, []),
            "estimated_vram_mb": row.estimated_vram_mb,
            "default_settings": _json(row.default_settings_json, {}),
            "updated_at": row.updated_at,
            "revision": row.revision,
        }

    @staticmethod
    def _vocabulary(resources) -> dict:
        return {
            "operations": sorted({value for row in resources for value in _json(row.operations_json, [])}),
            "domains": sorted({value for row in resources for value in _json(row.domains_json, [])}),
            "content_tags": sorted({value for row in resources for value in _json(row.content_tags_json, [])}),
            "features": sorted({value for row in resources for value in _json(row.features_json, [])}),
        }

    @staticmethod
    def _plan_row_values(source: str, requirements: dict, built: dict) -> dict:
        identity = built.get("identity_conditioning") or {}
        return {
            "source": source,
            "status": built["status"],
            "kind": requirements["kind"],
            "operation": requirements["operation"],
            "requirements_json": _wire_json(requirements),
            "selected_resources_json": _wire_json(built["selected_resources"]),
            "execution_options_json": _wire_json(built["execution_options"]),
            "explanation_json": _wire_json(built["explanation"]),
            "estimated_vram_mb": built["estimated_vram_mb"],
            "block_code": built["block_code"],
            "block_message": built["block_message"],
            "persona_id": identity.get("persona_id"),
            "identity_profile_id": identity.get("profile_id"),
            "identity_profile_revision": identity.get("profile_revision"),
            "identity_reference_id": identity.get("reference_id"),
            "identity_reference_sha256": identity.get("reference_sha256"),
            "identity_conditioning_json": _wire_json(identity),
        }

    @staticmethod
    def _plan_response_values(plan_id: str | None, source: str, requirements: dict, built: dict) -> dict:
        return {
            "id": plan_id,
            "source": source,
            "status": built["status"],
            "kind": requirements["kind"],
            "operation": requirements["operation"],
            "requirements": requirements,
            "selected_resources": built["selected_resources"],
            "explanation": built["explanation"],
            "estimated_vram_mb": built["estimated_vram_mb"],
            "identity_conditioning": public_identity_conditioning(built.get("identity_conditioning")),
            "block": (
                {"code": built["block_code"], "message": built["block_message"]}
                if built["block_code"] or built["block_message"]
                else None
            ),
            "created_at": None,
        }

    @staticmethod
    def _plan_response(row) -> dict:
        requirements = _json(row.requirements_json, {})
        selected = _json(row.selected_resources_json, [])
        explanation = _json(row.explanation_json, {})
        identity = _json(row.identity_conditioning_json, {})
        return {
            "id": row.id,
            "source": row.source,
            "status": row.status,
            "kind": row.kind,
            "operation": row.operation,
            "requirements": requirements if isinstance(requirements, dict) else {},
            "selected_resources": selected if isinstance(selected, list) else [],
            "explanation": explanation if isinstance(explanation, dict) else {},
            "estimated_vram_mb": row.estimated_vram_mb,
            "identity_conditioning": public_identity_conditioning(identity),
            "block": (
                {"code": row.block_code or "blocked", "message": row.block_message or "Media plan is blocked."}
                if row.block_code or row.block_message
                else None
            ),
            "created_at": row.created_at,
        }
