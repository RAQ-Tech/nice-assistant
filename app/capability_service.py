from __future__ import annotations

from dataclasses import dataclass
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
from app.provider_contracts import CancellationToken, ProviderError
from app.service_errors import ConflictError, NotFoundError, RequestError, ServiceError
from app.task_contracts import (
    CAPABILITY_PLANNING,
    CapabilityPlanningTaskInput,
    TaskContractError,
    MAX_OFFERED_PRESETS,
    PRESET_REFERENCE_PREFIX,
    AvailablePreset,
    ATTACHMENT_REFERENCE_PREFIX,
    EDIT_OPERATIONS,
    MASK_OPERATIONS,
    AvailableAttachment,
    AvailableCapability,
    PlannedCapability,
    is_high_confidence_image_edit_request,
    is_high_confidence_media_action_request,
)


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _identity_state(kind: str, result: dict | None) -> str:
    if kind != "image":
        return "not_applicable"
    identity = (result or {}).get("identityConditioning")
    if not isinstance(identity, dict):
        return "not_applicable"
    if identity.get("status") == "unconditioned":
        return "unconditioned"
    if identity.get("claim_status") == "verified" or identity.get("verification_status") == "passed":
        return "verified"
    return "unverified"


def attachment_response(row, frames=None) -> dict | None:
    if not row:
        return None
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "capability_request_id": row.capability_request_id,
        "media_id": row.media_id,
        "content_url": f"/api/v1/media/{row.media_id}" if row.media_id else None,
        # Additional frames of the same photo set, shown beside the first one.
        # Empty for every ordinary picture, which is most of them.
        "frames": [
            {
                "media_id": frame.media_id,
                "content_url": f"/api/v1/media/{frame.media_id}",
                "frame_index": frame.frame_index,
            }
            for frame in (frames or [])
        ],
        "identity_state": row.identity_state,
        "safe_error": row.safe_error,
        "retry_available": bool(row.retry_available),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


# How far back to read the persona's own words. Three is enough to catch "I got
# my nails done" one turn before "send me a picture", and short enough that a
# conversation which has moved on does not still be voting.
PERSONA_REPLY_WINDOW = 3


def _reply_text(repo, request) -> str:
    """What this persona has been saying lately, for ranking retained pictures.

    Not the reply on this turn. When a message passes the image-action gate -
    which is the only way a conversational picture request survives planning -
    the persona's prose is replaced by a neutral platform acknowledgement before
    it is stored, exactly as ADR 0021 requires. There is nothing to rank by
    there.

    The words that matter came earlier: the persona said it walked the dog, and
    then a picture was asked for. So this reads the recent transcript instead.
    Empty for anything without a chat, which includes every direct action and
    every background picture; those behave exactly as they did.
    """

    if not request.chat_id:
        return ""
    replies = [row.text for row in repo.messages(request.chat_id) if row.role == "assistant" and row.text]
    return " ".join(replies[-PERSONA_REPLY_WINDOW:])[:2000]


def _attachment_with_frames(repo, row) -> dict | None:
    """An attachment together with the other frames sent beside it."""

    if not row:
        return None
    return attachment_response(row, repo.attachment_frames(row.id))


def _sync_attachment(repo, request, state: str, *, message: str | None = None, result: dict | None = None) -> None:
    attachment = repo.chat_attachment_for_capability(request.user_id, request.id)
    if not attachment:
        return
    attachment.status = {
        "pending_confirmation": "queued",
        "denied": "cancelled",
        "expired": "cancelled",
    }.get(state, state)
    attachment.updated_at = now_ts()
    if result is not None:
        attachment.media_id = str(result.get("mediaId") or "") or None
        extra = [
            (str(frame.get("media_id") or ""), frame.get("frame_index"))
            for frame in (result.get("frames") or [])
            if frame.get("media_id")
        ]
        if extra:
            repo.add_attachment_frames(attachment.id, extra)
        attachment.identity_state = _identity_state(attachment.kind, result)
    if state == "failed":
        attachment.safe_error = redact_sensitive_text(message or "Image generation failed.")[:500]
        attachment.retry_available = 1
    elif state == "cancelled":
        attachment.safe_error = None
        attachment.retry_available = 1
    elif state in CAPABILITY_TERMINAL_STATES:
        attachment.safe_error = None
        attachment.retry_available = 0
    if state in CAPABILITY_TERMINAL_STATES:
        attachment.completed_at = now_ts()


class InvalidCapabilityTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class OfferedPresets:
    """The shortlist the planner was shown, and what those labels stand for."""

    available: tuple[AvailablePreset, ...]
    bindings: dict


@dataclass(frozen=True)
class OfferedAttachments:
    """What the planner was shown, and what those labels stand for.

    ``bindings`` never leaves the platform. Carrying it from planning to
    preparation keeps a reference pointing at the image the planner actually
    saw, even if another image completes in the meantime.
    """

    available: tuple[AvailableAttachment, ...]
    bindings: dict


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
    _sync_attachment(repo, request, state, message=message, result=result)
    repo.add_capability_event(
        request,
        action,
        from_status=previous,
        to_status=state,
        detail={"code": code} if code else {},
    )


# A planning window wide enough to resolve "the colours we talked about"
# without turning capability planning into a second conversation history.
PLANNING_CONTEXT_MESSAGES = 6
PLANNING_CONTEXT_MESSAGE_CHARACTERS = 400
PLANNING_CONTEXT_CHARACTERS = 1200


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
        provider_service=None,
        identity_service=None,
        task_models=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.registry = registry
        self.jobs = jobs
        self.media = media
        self.media_catalog = media_catalog
        self.logger = logger
        self.provider_url_policy = provider_url_policy
        self.provider_service = provider_service
        self.task_models = task_models
        self.identity_service = identity_service

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

    def _editable_operations(self, user_id: str) -> tuple[str, ...]:
        return tuple(
            operation
            for operation in EDIT_OPERATIONS
            if self.media_catalog.has_ready_operation(user_id, "image", operation)
        )

    def planning_presets(self, user_id: str, kind: str = "image") -> OfferedPresets:
        """Publish a bounded shortlist of enabled presets as opaque labels.

        This is a coarse pre-filter: the request's operation and features are not
        known until the model answers. The full hard filter still runs at plan
        time and may reject the model's choice, in which case selection falls
        back to the deterministic score.
        """

        presets = [item for item in self.media_catalog.presets(user_id, kind=kind) if item["enabled"]]
        presets = presets[:MAX_OFFERED_PRESETS]
        available = []
        bindings = {}
        for index, preset in enumerate(presets, start=1):
            reference = f"{PRESET_REFERENCE_PREFIX}{index}"
            bindings[reference] = preset["id"]
            available.append(
                AvailablePreset(
                    reference=reference,
                    title=preset["name"],
                    routing_card=preset["routing_card"] or "",
                )
            )
        return OfferedPresets(tuple(available), bindings)

    def routing_preview(self, user_id: str, text: str, kind: str = "image") -> dict:
        """Run real routing for a pasted message and report every step.

        Deliberately temporary tooling. Authoring a routing card is otherwise
        guesswork - there is no way to see whether the sentence you wrote makes
        the preset you meant win. It runs the same shortlist, the same Task
        Model role, and the same planner a real turn does, so what it shows is
        what would actually happen, not a simulation of it.
        """

        message = " ".join(str(text or "").split()).strip()
        if not message:
            raise RequestError("enter a message to test routing against", 400)
        offered = self.planning_presets(user_id, kind)
        shortlist = [
            {"reference": item.reference, "title": item.title, "routing_card": item.routing_card}
            for item in offered.available
        ]
        definitions = self.planning_definitions(user_id)
        result = {
            "message": message[:2000],
            "shortlist": shortlist,
            "requested": False,
            "task_model": {"ran": False, "error": "", "chose": ""},
            "plan": None,
        }
        if not definitions:
            result["task_model"]["error"] = "No image capability is available, so nothing would be planned."
            return result
        vocabulary = self.planning_vocabulary(user_id)
        chosen_reference = ""
        planned = None
        try:
            outcome = self.task_models.run(
                user_id,
                CAPABILITY_PLANNING,
                CapabilityPlanningTaskInput(
                    user_text=message,
                    available_capabilities=definitions,
                    available_operations=tuple(vocabulary.get("operations") or ("generate",)),
                    available_domains=tuple(vocabulary.get("domains") or ()),
                    available_content_tags=tuple(vocabulary.get("content_tags") or ()),
                    available_features=tuple(vocabulary.get("features") or ()),
                    available_presets=offered.available,
                ),
                CancellationToken(),
            )
            result["task_model"]["ran"] = not outcome.fallback_used
            if outcome.fallback_used:
                # The service applies a fallback policy rather than raising, so
                # a provider failure would otherwise look like "no image
                # wanted" - which is the wrong thing to go and fix.
                result["task_model"]["error"] = (
                    "The task model did not answer, so its configured fallback was used. Routing would fall back "
                    "to the deterministic score."
                )
            planned = next(
                (item for item in outcome.output.requests if item.capability_key == "media.generate_image"),
                None,
            )
        except (ProviderError, TaskContractError, ServiceError) as exc:
            # A tester that hid this would be worse than no tester: not routing
            # at all is the most common reason a preset never wins.
            result["task_model"]["error"] = getattr(exc, "user_message", None) or str(exc)
        if not planned:
            if not result["task_model"]["error"]:
                result["task_model"]["error"] = "The task model did not request an image for this message."
            return result
        result["requested"] = True
        chosen_reference = planned.preset or ""
        result["task_model"]["chose"] = chosen_reference
        result["plan"] = self.media_catalog.preview(
            user_id,
            {
                "kind": kind,
                "operation": planned.operation,
                "domains": list(planned.domains),
                "content_tags": list(planned.content_tags),
                "required_features": list(planned.required_features),
                "preferred_preset_id": offered.bindings.get(chosen_reference, ""),
            },
        )
        return result

    def planning_context(self, user_id: str, chat_id: str | None) -> tuple[str, ...]:
        """Return recent user messages so a request can resolve its own references.

        Only the user's own words. ADR 0017 excludes persona reply prose from
        planning so a persona cannot invent or widen a media subject, and that
        reason is unchanged - this widens the window over what the user said,
        nothing else.
        """

        if not chat_id:
            return ()
        with self._uow() as uow:
            if not uow.repo.chat(user_id, chat_id):
                return ()
            rows = uow.repo.messages(chat_id)
        recent = [row for row in rows if row.role == "user"][-PLANNING_CONTEXT_MESSAGES:]
        window = []
        budget = PLANNING_CONTEXT_CHARACTERS
        # Newest first while spending the budget, so the messages most likely to
        # be referenced survive when the allowance runs out.
        for row in reversed(recent):
            text = " ".join(str(row.text or "").split()).strip()
            if not text:
                continue
            text = text[:PLANNING_CONTEXT_MESSAGE_CHARACTERS]
            if len(text) > budget:
                break
            budget -= len(text)
            window.append(text)
        return tuple(reversed(window))

    def planning_attachments(self, user_id: str, chat_id: str | None) -> OfferedAttachments:
        """Publish this chat's editable images as opaque references.

        The task model receives labels and short descriptions only. Media
        identity stays on the platform side, and the caller carries the returned
        bindings through to preparation so a reference always resolves to the
        image the planner was actually shown.
        """

        if not chat_id:
            return OfferedAttachments((), {})
        with self._uow() as uow:
            bindings = self._attachment_bindings(uow.repo, user_id, chat_id)
        return OfferedAttachments(
            tuple(offered for offered, _ in bindings),
            {offered.reference: media_id for offered, media_id in bindings},
        )

    def _attachment_bindings(self, repo, user_id: str, chat_id: str) -> list[tuple[AvailableAttachment, str]]:
        bindings = []
        for index, row in enumerate(repo.editable_chat_attachments(user_id, chat_id), start=1):
            request = repo.capability_request(user_id, row.capability_request_id)
            prompt = str(_json_object(request.arguments_json).get("prompt") or "").strip() if request else ""
            summary = " ".join(prompt.split())[:120] or "an image in this conversation"
            position = "most recent image" if index == 1 else f"image {index} counting back from the most recent"
            bindings.append(
                (
                    AvailableAttachment(f"{ATTACHMENT_REFERENCE_PREFIX}{index}", f"The {position}: {summary}"),
                    str(row.media_id),
                )
            )
        return bindings

    @staticmethod
    def _resolve_attachments(request: PlannedCapability, attachments: dict) -> tuple[str, str]:
        """Turn offered references into owner-scoped media identifiers."""

        editing = request.operation in EDIT_OPERATIONS
        if not editing:
            if request.source_attachment or request.mask_attachment:
                raise RequestError("attachments are only valid for image editing", 400)
            return "", ""
        source = attachments.get(request.source_attachment or "")
        if not source:
            raise RequestError("the referenced source image is no longer available", 409)
        if request.operation not in MASK_OPERATIONS:
            if request.mask_attachment:
                raise RequestError("this operation does not accept a mask", 400)
            return source, ""
        mask = attachments.get(request.mask_attachment or "")
        if not mask:
            raise RequestError("the referenced mask image is no longer available", 409)
        if mask == source:
            raise RequestError("the mask must differ from the source image", 400)
        return source, mask

    def planning_definitions(
        self,
        user_id: str,
        *,
        allow_images: bool = True,
        allow_edits: bool = False,
    ) -> tuple[AvailableCapability, ...]:
        enabled = self._enabled_keys(user_id)
        return tuple(
            AvailableCapability(item.key, item.title, item.description)
            for item in self.registry.definitions()
            if item.key in enabled
            and (allow_edits or item.key != "media.edit_image")
            and (allow_images or item.key != "media.generate_image")
        )

    def planning_vocabulary(self, user_id: str, *, allow_edits: bool = False) -> dict:
        vocabulary = self.media_catalog.vocabulary(user_id)
        enabled = self._enabled_keys(user_id)
        if "media.generate_image" in enabled:
            vocabulary["features"] = sorted(set(vocabulary.get("features") or ()) | {IDENTITY_CONTROL_FEATURE})
        operations = ["generate"]
        if allow_edits and "media.edit_image" in enabled:
            operations.extend(self._editable_operations(user_id))
        vocabulary["operations"] = operations
        return vocabulary

    def definitions(self, user_id: str) -> list[dict]:
        enabled = self._enabled_keys(user_id)
        return [{**item.public(), "available": item.key in enabled} for item in self.registry.definitions()]

    def _ready_media_backends(self, repo, user_id: str, kind: str):
        """Check fallback candidates only when multiple explicit catalog backends exist."""

        if not self.provider_service:
            return None
        candidates = sorted(
            {
                (row.provider_key, row.backend)
                for row in repo.media_catalog_resources(user_id, enabled=True)
                if row.resource_type == "model" and row.kind == kind
            }
        )
        if len(candidates) <= 1:
            return None
        ready = set()
        for provider_key, backend in candidates:
            check_key = backend if provider_key == "local-image" else "openai"
            checked = self.provider_service.check(user_id, check_key)
            if checked and checked.get("ok"):
                ready.add((provider_key, backend))
        return ready

    def media_readiness(self, user_id: str) -> dict:
        """Return an everyday image readiness summary without exposing catalog internals."""

        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {}
            preferences = settings.get("preferences") or {}
            configured = str(preferences.get("image_provider") or "disabled").strip().lower()
            local_backend = str(preferences.get("image_local_backend") or "automatic1111").strip().lower()
        provider_key = {
            "local/automatic1111": "automatic1111",
            "local/comfyui": "comfyui",
            "local": local_backend,
            "a1111": "automatic1111",
        }.get(configured, configured)
        provider = {
            "key": provider_key,
            "reachable": False,
            "status": "disabled",
            "message": "Choose an image provider to enable image generation.",
        }
        if provider_key != "disabled" and self.provider_service:
            checked = self.provider_service.check(user_id, provider_key)
            if checked:
                provider = {
                    "key": provider_key,
                    "reachable": bool(checked.get("ok")),
                    "status": str(checked.get("status") or "unknown"),
                    "message": str(checked.get("message") or "Provider readiness is unknown."),
                }
        catalog_ready = self.media_catalog.has_ready_resource(user_id, "image")
        basic_ready = bool(catalog_ready and provider["reachable"])
        identity = {
            "ready": False,
            "status": "optional",
            "message": "Optional identity matching is not configured. Basic images are still available.",
        }
        vocabulary = self.media_catalog.vocabulary(user_id)
        if IDENTITY_CONTROL_FEATURE in (vocabulary.get("features") or []) and self.identity_service:
            try:
                checked_identity = self.identity_service.check_provider(user_id)
            except ConflictError:
                checked_identity = {
                    "ready": False,
                    "status": "incomplete",
                    "message": "Optional identity matching settings are incomplete.",
                }
            identity = {
                "ready": bool(checked_identity.get("ready")),
                "status": str(checked_identity.get("status") or "unknown"),
                "message": str(checked_identity.get("message") or "Optional identity readiness is unknown."),
            }
        return {
            "provider": provider,
            "basic_generation": {
                "ready": basic_ready,
                "message": (
                    "Images are ready."
                    if basic_ready
                    else provider["message"]
                    if not provider["reachable"]
                    else "The provider is reachable, but no basic image workflow is ready."
                ),
            },
            "optional_identity": identity,
        }

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
                ready_backends=self._ready_media_backends(uow.repo, user_id, definition.kind),
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
        user_text: str,
        originating_persona_id: str | None,
        planned: list[PlannedCapability],
        source: str = "task_model",
        offered_attachments: dict | None = None,
        offered_presets: dict | None = None,
        planning_context: tuple[str, ...] = (),
    ) -> list[dict]:
        chat = repo.chat(user_id, chat_id)
        if not chat:
            raise NotFoundError("chat not found")
        turn = repo.turn_by_id(turn_id)
        if not turn or not turn.assistant_message_id:
            raise ConflictError("The assistant reply must be durable before media can be attached.")
        # Resolve against what the planner was shown, so a newly completed image
        # cannot silently shift what a reference means, then confirm the result
        # is still an editable attachment of this chat.
        current = {media_id for _, media_id in self._attachment_bindings(repo, user_id, chat_id)}
        offered = dict(offered_attachments) if offered_attachments is not None else {}
        attachments = {reference: media_id for reference, media_id in offered.items() if media_id in current}
        prepared = []
        for index, request in enumerate(planned):
            definition = self.registry.by_key(request.capability_key)
            editing = request.operation in EDIT_OPERATIONS
            # Editing uses its own narrower gate. A proposed edit still needs
            # confirmation, so this decides what may be offered, not what runs.
            gate = is_high_confidence_image_edit_request if editing else is_high_confidence_media_action_request
            if not gate(user_text):
                continue
            try:
                source_media_id, mask_media_id = self._resolve_attachments(request, attachments)
            except RequestError:
                # A reference that no longer resolves means the conversation
                # changed under the plan. Drop the request instead of silently
                # editing a different image or degrading into a generation.
                continue
            auto_execute = definition.permission_mode == "auto"
            status = "queued" if auto_execute else "pending_confirmation"
            permission_mode = definition.permission_mode
            requirements = self.registry.requirements(definition, {"prompt": request.prompt})
            requirements = requirements.__class__(
                kind=requirements.kind,
                prompt=requirements.prompt,
                operation=request.operation,
                domains=request.domains,
                content_tags=request.content_tags,
                required_features=request.required_features,
                source_media_id=source_media_id,
                mask_media_id=mask_media_id,
                planning_context=planning_context,
                scene=tuple(sorted((request.scene or {}).items())),
                preferred_preset_id=(offered_presets or {}).get(request.preset, ""),
            )
            row, created = repo.add_capability_request(
                user_id=user_id,
                chat_id=chat_id,
                turn_id=turn_id,
                capability_key=definition.key,
                arguments=requirements.as_arguments(),
                status=status,
                permission_mode=permission_mode,
                idempotency_key=f"turn:{turn_id}:task:{index}:{definition.key}",
            )
            job = repo.job_for_capability(row.id)
            if created:
                requirement_values = {
                    "kind": requirements.kind,
                    "operation": requirements.operation,
                    "domains": requirements.domains,
                    "content_tags": requirements.content_tags,
                    "required_features": requirements.required_features,
                    "preferred_preset_id": requirements.preferred_preset_id,
                }
                if requirements.operation in EDIT_OPERATIONS:
                    # Editing selects a workflow with real source and mask
                    # bindings; the coordinator plan cannot express those.
                    plan = self.media_catalog.create_edit_plan(repo, user_id, row.id, requirement_values)
                else:
                    plan = self.media_catalog.create_coordinator_plan(
                        repo,
                        user_id,
                        row.id,
                        requirement_values,
                        persona_id=originating_persona_id,
                        ready_backends=self._ready_media_backends(repo, user_id, definition.kind),
                    )
                repo.add_capability_event(
                    row,
                    "requested",
                    from_status=None,
                    to_status=row.status,
                    detail={
                        "source": source,
                        "media_plan_status": plan.status,
                        "originating_persona_id": originating_persona_id,
                    },
                )
                repo.add_chat_attachment(
                    user_id=user_id,
                    chat_id=chat_id,
                    assistant_message_id=turn.assistant_message_id,
                    capability_request_id=row.id,
                    kind=definition.kind,
                    status="queued",
                )
                if auto_execute and plan.status == "ready":
                    job = repo.add_job(
                        user_id=user_id,
                        chat_id=chat_id,
                        turn_id=None,
                        kind=definition.kind,
                        progress="Queued",
                        capability_request_id=row.id,
                    )
                    repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
                elif auto_execute:
                    transition_capability(
                        repo,
                        row,
                        "failed",
                        "failed",
                        code=plan.block_code or "plan_blocked",
                        message=plan.block_message or "Image generation is not ready.",
                    )
            response = self._response(repo, row, job=job)
            if created and auto_execute and job and row.status == "queued":
                response["auto_submit"] = True
            prepared.append(response)
        return prepared

    def prepare_background_request(
        self,
        repo,
        *,
        user_id: str,
        persona_id: str,
        scene: dict,
        prompt: str,
        entry_id: str,
        seed: int | None = None,
        photo_set_id: str = "",
        frame_index: int | None = None,
    ) -> tuple[str, str] | None:
        """Create a chat-less request for a picture nobody has asked for.

        It is a real capability request rather than a direct write to the
        library, because the execution plan, the generation journal, the audit
        history, and cancellation all hang off one. A background picture that
        skipped it would be the only picture in the product with no record of
        how it was made, which is the opposite of the point.

        Returns the request and job ids to submit once the transaction commits,
        or nothing if no plan could be built - in which case the caller leaves
        the scene approved for another night.
        """

        definition = self.registry.by_kind("image")
        requirements = self.registry.requirements(definition, {"prompt": prompt})
        requirements = requirements.__class__(
            kind=requirements.kind,
            prompt=requirements.prompt,
            operation="generate",
            required_features=(IDENTITY_CONTROL_FEATURE,),
            scene=tuple(sorted((scene or {}).items())),
        )
        arguments = requirements.as_arguments()
        if seed is not None:
            # Pinned rather than random, so a frame relates to its set and can be
            # regenerated as the same picture.
            arguments["seed"] = int(seed)
        if photo_set_id:
            arguments["photo_set_id"] = photo_set_id
            arguments["frame_index"] = int(frame_index or 0)
        row, created = repo.add_capability_request(
            user_id=user_id,
            chat_id=None,
            turn_id=None,
            capability_key=definition.key,
            arguments=arguments,
            status="queued",
            # The operator approved this scene. That approval is the permission,
            # and there is no conversation in which to ask for another one.
            permission_mode="explicit",
            idempotency_key=f"scene:{entry_id}",
        )
        if not created:
            # This scene was already produced, or attempted, on an earlier run.
            return None
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
                "preferred_preset_id": "",
            },
            persona_id=persona_id,
            ready_backends=self._ready_media_backends(repo, user_id, definition.kind),
        )
        repo.add_capability_event(
            row,
            "requested",
            from_status=None,
            to_status=row.status,
            detail={
                "source": "scene_backlog",
                "media_plan_status": plan.status,
                "originating_persona_id": persona_id,
                "scene_backlog_entry_id": entry_id,
                "photo_set_id": photo_set_id or None,
                "frame_index": frame_index,
            },
        )
        if plan.status != "ready":
            transition_capability(
                repo,
                row,
                "failed",
                "failed",
                code=plan.block_code or "plan_blocked",
                message=plan.block_message or "Background image generation is not ready.",
            )
            return None
        job = repo.add_job(
            user_id=user_id,
            chat_id=None,
            turn_id=None,
            kind=definition.kind,
            progress="Queued",
            capability_request_id=row.id,
        )
        repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
        return row.id, job.id

    def submit_background(self, user_id: str, request_id: str, job_id: str, on_settled=None) -> None:
        """Submit an already-planned background request as bulk work."""

        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row or row.status != "queued":
                return
            values = _json_object(row.arguments_json)
            execution_spec = self.media_catalog.execution_spec(uow.repo, user_id, row.id)
            values.update(execution_spec["options"])
            values["_estimated_vram_mb"] = execution_spec["estimated_vram_mb"]
        self._submit(
            request_id,
            job_id,
            "image",
            user_id,
            None,
            values,
            latency_class="bulk",
            on_settled=on_settled,
        )

    def submit_queued(self, user_id: str, request_id: str) -> dict | None:
        """Submit a durable auto-approved request after its creating transaction commits."""

        submit = False
        values: dict = {}
        kind = ""
        chat_id = None
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            job = uow.repo.job_for_capability(row.id)
            definition = self.registry.by_key(row.capability_key)
            kind = definition.kind
            chat_id = row.chat_id
            if row.status == "queued" and job and job.status == "queued":
                values = _json_object(row.arguments_json)
                execution_spec = self.media_catalog.execution_spec(uow.repo, user_id, row.id)
                values.update(execution_spec["options"])
                values["_estimated_vram_mb"] = execution_spec["estimated_vram_mb"]
                # ADR 0033: read here, not persisted onto the request and never
                # sent to a task model. It only ranks retained pictures that
                # already qualified, so it cannot introduce or widen anything.
                values["_reply_text"] = _reply_text(uow.repo, row)
                submit = True
            response = self._response(uow.repo, row, job=job)
        if submit:
            self._submit(request_id, job.id, kind, user_id, chat_id, values)
        return response

    def fail_queued_submission(self, user_id: str, request_id: str) -> dict | None:
        with self._uow() as uow:
            row = uow.repo.capability_request(user_id, request_id)
            if not row:
                return None
            if row.status == "queued":
                transition_capability(
                    uow.repo,
                    row,
                    "failed",
                    "failed",
                    code="submission_failed",
                    message="Image generation could not start. You can retry it.",
                )
            return self._response(uow.repo, row)

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
            chat = uow.repo.chat(user_id, chat_id) if chat_id else None
            if chat_id and not chat:
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
                if chat_id:
                    assistant = uow.repo.add_message(chat_id, "assistant", "")
                    chat.updated_at = now_ts()
                    uow.repo.add_chat_attachment(
                        user_id=user_id,
                        chat_id=chat_id,
                        assistant_message_id=assistant.id,
                        capability_request_id=row.id,
                        kind=kind,
                        status="queued",
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

    def retry(self, user_id: str, request_id: str) -> dict | None:
        submit = False
        kind = ""
        chat_id = None
        values: dict = {}
        with self._uow() as uow:
            original = uow.repo.capability_request(user_id, request_id)
            if not original:
                return None
            attachment = uow.repo.chat_attachment_for_capability(user_id, original.id)
            if not attachment or not attachment.retry_available or original.status not in {"failed", "cancelled"}:
                raise ConflictError("This image cannot be retried.")
            definition = self.registry.by_key(original.capability_key)
            if definition.kind != "image" or not original.chat_id:
                raise ConflictError("Only failed chat images can be retried here.")
            chat = uow.repo.chat(user_id, original.chat_id)
            if not chat:
                raise NotFoundError("chat not found")
            auto_execute = True
            status = "queued"
            permission_mode = "auto"
            arguments = _json_object(original.arguments_json)
            row, _created = uow.repo.add_capability_request(
                user_id=user_id,
                chat_id=original.chat_id,
                turn_id=None,
                capability_key=original.capability_key,
                arguments=arguments,
                status=status,
                permission_mode=permission_mode,
                idempotency_key=f"retry:{original.id}:{secrets.token_hex(12)}",
                retry_of_request_id=original.id,
            )
            prior_plan = uow.repo.media_execution_plan_for_capability(user_id, original.id)
            if prior_plan and prior_plan.source == "coordinator":
                plan = self.media_catalog.create_coordinator_plan(
                    uow.repo,
                    user_id,
                    row.id,
                    {
                        "kind": definition.kind,
                        "operation": arguments.get("operation") or "generate",
                        "domains": arguments.get("domains") or [],
                        "content_tags": arguments.get("content_tags") or [],
                        "required_features": arguments.get("required_features") or [],
                    },
                    persona_id=chat.persona_id,
                    ready_backends=self._ready_media_backends(uow.repo, user_id, definition.kind),
                )
            else:
                plan = self.media_catalog.create_manual_plan(
                    repo=uow.repo,
                    user_id=user_id,
                    capability_request_id=row.id,
                    kind=definition.kind,
                )
            uow.repo.add_capability_event(
                row,
                "requested",
                from_status=None,
                to_status=row.status,
                detail={"source": "retry", "retry_of": original.id, "media_plan_status": plan.status},
            )
            assistant = uow.repo.add_message(original.chat_id, "assistant", "")
            chat.updated_at = now_ts()
            uow.repo.add_chat_attachment(
                user_id=user_id,
                chat_id=original.chat_id,
                assistant_message_id=assistant.id,
                capability_request_id=row.id,
                kind=definition.kind,
                status="queued",
            )
            attachment.status = "retried"
            attachment.retry_available = 0
            attachment.updated_at = now_ts()
            uow.repo.add_capability_event(
                original,
                "retried",
                from_status=original.status,
                to_status=original.status,
                detail={"retry_request_id": row.id},
            )
            job = None
            if auto_execute and plan.status == "ready":
                job = uow.repo.add_job(
                    user_id=user_id,
                    chat_id=original.chat_id,
                    turn_id=None,
                    kind=definition.kind,
                    progress="Queued",
                    capability_request_id=row.id,
                )
                uow.repo.add_capability_event(row, "queued", from_status="queued", to_status="queued")
                execution_spec = self.media_catalog.execution_spec(uow.repo, user_id, row.id)
                values = dict(arguments)
                values.update(execution_spec["options"])
                values["_estimated_vram_mb"] = execution_spec["estimated_vram_mb"]
                submit = True
            elif auto_execute:
                transition_capability(
                    uow.repo,
                    row,
                    "failed",
                    "failed",
                    code=plan.block_code or "plan_blocked",
                    message=plan.block_message or "Image generation is not ready.",
                )
            response = self._response(uow.repo, row, job=job)
            kind = definition.kind
            chat_id = original.chat_id
        if submit:
            self._submit(response["id"], job.id, kind, user_id, chat_id, values)
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
            if definition.kind == "image":
                raise ConflictError("Image requests run without per-image approval. Retry this picture instead.")
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

    def _submit(
        self,
        request_id: str,
        job_id: str,
        kind: str,
        user_id: str,
        chat_id: str | None,
        values: dict,
        *,
        latency_class: str = "",
        on_settled=None,
    ):
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
            if on_settled:
                on_settled(repo, "completed", (result or {}).get("mediaId") or "")
            return result

        def on_failure(repo, code, message):
            row = repo.capability_request_by_id(request_id)
            if row and row.status not in CAPABILITY_TERMINAL_STATES:
                transition_capability(repo, row, "failed", "failed", code=code, message=message)
            if on_settled:
                on_settled(repo, "failed", "")

        def on_cancel(repo):
            row = repo.capability_request_by_id(request_id)
            if row and row.status not in CAPABILITY_TERMINAL_STATES:
                transition_capability(repo, row, "cancelled", "cancelled")
            if on_settled:
                on_settled(repo, "cancelled", "")

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
                latency_class=latency_class or ("bulk" if kind == "video" else "standard"),
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
            "permission_mode": row.permission_mode_effective,
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
            "retry_of_request_id": row.retry_of_request_id,
            "attachment": _attachment_with_frames(repo, repo.chat_attachment_for_capability(row.user_id, row.id)),
            "media_plan": self.media_catalog.plan_for_capability(repo, row.user_id, row.id),
        }
