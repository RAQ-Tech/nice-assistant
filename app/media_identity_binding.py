"""Binding a persona's approved identity into an image plan.

Moved out of `MediaCatalogService`, which plans and stores; this is the part
that decides whether a plan may show a particular face, and how. It refuses
rather than degrades: a plan that cannot bind the identity it needs is blocked
with the reason, never rendered as somebody else.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from app.identity_conditioning import (
    IDENTITY_CONDITIONING_MODE,
    IDENTITY_CONTROL_FEATURE,
    IDENTITY_UNCONDITIONED_MODE,
)
from app.identity_images import MAX_REFERENCE_BYTES, read_identity_image_file
from app.media_planner import _json, build_media_plan
from app.service_errors import ConflictError, RequestError


class IdentityBinding:
    providers: Any

    @staticmethod
    def _with_identity_mechanism(repo, user_id: str, requirements: dict, persona_id: str | None) -> dict:
        """Require the mechanism this persona's Identity Spec declares.

        Only for a genuine persona image. An unrelated picture in a persona chat
        has no identity requirement to honor, per ADR 0017.
        """

        if IDENTITY_CONTROL_FEATURE not in requirements["required_features"] or not persona_id:
            return requirements
        identity = repo.visual_identity(user_id, persona_id)
        if not identity:
            return requirements
        return {**requirements, "required_identity_mechanism": identity.conditioning_mechanism}

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
        # The fallback is explicitly unconditioned, so the mechanism requirement
        # goes with the feature it belonged to.
        relaxed.pop("required_identity_mechanism", None)
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
            "conditioning_mechanism": identity.conditioning_mechanism if identity else None,
            "comparison_retry_enabled": bool(identity.comparison_retry_enabled) if identity else False,
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
        # A graph declares how many photos it can take by how many image inputs
        # it binds. PhotoMaker stacks a batch into a stronger likeness;
        # InstantID uses one. Anything beyond the slots is not pinned, because
        # a picture's record must name what actually made it.
        references = [row for row in repo.approved_identity_references(user_id, identity.id) if row.local_path]
        references = references[: max(1, len(bindings))]
        reference = references[0] if references else None
        if not reference or not reference.local_path:
            return self._block_identity(
                built,
                "identity_reference_unavailable",
                "The selected persona needs an approved identity reference.",
                persona_id=persona_id,
                profile=identity,
            )
        for row in references:
            try:
                content = read_identity_image_file(Path(row.local_path), max_bytes=MAX_REFERENCE_BYTES)
            except RequestError:
                return self._block_identity(
                    built,
                    "identity_reference_unavailable",
                    "The approved identity reference file is unavailable.",
                    persona_id=persona_id,
                    profile=identity,
                )
            if sha256(content).hexdigest() != row.sha256:
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
            # The whole set, pinned by checksum, so the record of a picture
            # names every photo that shaped the face rather than the first one.
            "references": [{"id": row.id, "sha256": row.sha256} for row in references],
            "workflow_resource_id": workflow["id"],
            "acceptance_threshold": float(identity.acceptance_threshold),
            "max_generation_attempts": int(identity.max_generation_attempts),
            "conditioning_mechanism": identity.conditioning_mechanism,
            "comparison_retry_enabled": bool(identity.comparison_retry_enabled),
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
                        "The persona identity profile changed after this request was planned. Retry the request before execution."
                    )
            elif identity:
                raise ConflictError(
                    "The persona identity profile changed after this request was planned. Retry the request before execution."
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
                "The persona identity profile changed after this request was planned. Retry the request before execution."
            )
        # Every pinned photo is checked, not just the first. A set where one
        # member changed is a different set, and the picture's record would
        # otherwise name a photo that no longer made it.
        paths, digests = IdentityBinding._verified_reference_files(repo, user_id, identity, snapshot)
        bindings = snapshot.get("identity_image_bindings")
        if options.get("backend") != "comfyui" or not isinstance(bindings, list) or not bindings:
            raise ConflictError("The selected adapter cannot execute the identity-aware workflow.")
        IdentityBinding._place_identity_bindings(options, snapshot, bindings)
        options["_identity_reference_paths"] = paths
        options["_identity_reference_sha256s"] = digests
        options["_identity_reference_path"] = paths[0]
        options["_identity_reference_sha256"] = digests[0]
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

    @staticmethod
    def _place_identity_bindings(options: dict, snapshot: dict, bindings: list) -> None:
        """Give the bindings to the pass whose graph actually has those nodes.

        A preset may do the identity work in a later pass - generate the scene,
        then apply the face. Writing the bindings at the top level put that
        pass's node IDs into the first pass's graph, where they do not exist,
        so every multi-pass identity preset failed at upload time.
        """

        stages = options.get("stages") or []
        if not stages:
            options["identity_image_bindings"] = bindings
            return
        owner = next(
            (stage for stage in stages if stage.get("workflow_resource_id") == snapshot.get("workflow_resource_id")),
            None,
        )
        if owner is None:
            raise ConflictError(
                "The identity workflow is no longer one of this preset's passes. Retry the request before execution."
            )
        owner["identity_image_bindings"] = bindings
        # The first pass runs from the top-level values rather than from its
        # stage entry, so it is the only one that sets them there.
        options["identity_image_bindings"] = bindings if owner is stages[0] else []

    @staticmethod
    def _verified_reference_files(repo, user_id: str, identity, snapshot: dict) -> tuple[list[str], list[str]]:
        """Re-check every photo this plan pinned, and return where they live.

        Every one, not just the first. A set where one member changed is a
        different set, and the picture's record would otherwise name a photo
        that no longer helped make it.
        """

        pinned = snapshot.get("references") or [
            {"id": snapshot.get("reference_id"), "sha256": snapshot.get("reference_sha256")}
        ]
        paths = []
        digests = []
        for entry in pinned:
            reference = repo.identity_reference(user_id, entry.get("id"))
            if (
                not reference
                or reference.identity_id != identity.id
                or reference.persona_id != identity.persona_id
                or reference.review_status != "approved"
                or reference.sha256 != entry.get("sha256")
                or not reference.local_path
            ):
                raise ConflictError(
                    "The approved identity reference changed after this request was planned. "
                    "Retry the request before execution."
                )
            try:
                content = read_identity_image_file(Path(reference.local_path), max_bytes=MAX_REFERENCE_BYTES)
            except RequestError as exc:
                raise ConflictError("The approved identity reference file is unavailable.") from exc
            if sha256(content).hexdigest() != reference.sha256:
                raise ConflictError("The approved identity reference no longer matches its reviewed content.")
            paths.append(str(Path(reference.local_path)))
            digests.append(reference.sha256)
        return paths, digests
