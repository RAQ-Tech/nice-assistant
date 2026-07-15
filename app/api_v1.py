from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.application import ApplicationServices
from app.resource_service import AuthContext
from app.runtime import SESSION_COOKIE
from app.security import request_client_address
from app.service_errors import AuthenticationError, NotFoundError, RequestError


router = APIRouter(prefix="/api/v1")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=512)


class SettingsUpdate(StrictModel):
    global_default_model: str | None = None
    default_memory_mode: str = Field(default="saved", pattern="^(off|saved)$")
    stt_provider: str = "disabled"
    tts_provider: str = "disabled"
    tts_format: str = "wav"
    openai_api_key: str | None = None
    onboarding_done: bool = False
    preferences: dict = Field(default_factory=dict)


class WorkspaceWrite(StrictModel):
    name: str = Field(min_length=1, max_length=160)


class PersonaWrite(StrictModel):
    workspace_id: str
    workspace_ids: list[str] | None = None
    name: str = Field(min_length=1, max_length=160)
    avatar_url: str | None = None
    system_prompt: str | None = None
    personality_details: str | None = None
    traits: dict = Field(default_factory=dict)
    default_model: str | None = None
    preferred_voice: str | None = None
    preferred_tts_model: str | None = None
    preferred_tts_speed: str | None = None
    preferred_voice_openai: str | None = None
    preferred_tts_model_openai: str | None = None
    preferred_tts_speed_openai: str | None = None
    preferred_voice_local: str | None = None
    preferred_tts_model_local: str | None = None
    preferred_tts_speed_local: str | None = None


class MemoryCreate(StrictModel):
    scope: str = Field(pattern="^(global|workspace|persona|chat)$")
    scope_id: str | None = None
    content: str = Field(min_length=1, max_length=8000)


class MemoryUpdate(StrictModel):
    scope: str | None = Field(default=None, pattern="^(global|workspace|persona|chat)$")
    scope_id: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=8000)


class MemoryBulkAction(StrictModel):
    action: Literal["forget", "delete"]
    ids: list[str] = Field(min_length=1, max_length=2000)


class ChatBulkAction(StrictModel):
    action: Literal["hide", "delete"]
    ids: list[str] = Field(min_length=1, max_length=2000)


class BulkActionRepresentation(BaseModel):
    action: str
    requested_count: int
    affected_count: int
    ids: list[str]


class MemoryRepresentation(BaseModel):
    id: str
    scope: str
    scope_id: str | None = None
    content: str
    status: str
    confidence: float | None = None
    source_type: str
    source_message_id: str | None = None
    source_turn_id: str | None = None
    extractor_provider: str | None = None
    extractor_model: str | None = None
    extractor_version: str | None = None
    supersedes_id: str | None = None
    created_at: int
    updated_at: int
    reviewed_at: int | None = None
    forgotten_at: int | None = None
    can_undo: bool = False


class MemoryEventRepresentation(BaseModel):
    id: str
    memory_id: str
    related_memory_id: str | None = None
    action: str
    from_status: str | None = None
    to_status: str | None = None
    created_at: int
    undone_at: int | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryRepresentation]


class MemoryHistoryResponse(BaseModel):
    memory: MemoryRepresentation
    events: list[MemoryEventRepresentation]


class ChatCreate(StrictModel):
    workspace_id: str | None = None
    persona_id: str | None = None
    model: str | None = None
    memory_mode: str = Field(default="saved", pattern="^(off|saved)$")
    title: str = "New chat"


class ChatUpdate(StrictModel):
    title: str | None = None
    model_override: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    persona_id: str | None = None
    hidden_in_ui: bool | None = None


class ModelGenerationSettings(StrictModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    num_predict: int | None = Field(default=None, ge=1, le=8192)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    context_window_tokens: int | None = Field(default=None, ge=2048, le=262144)


class TurnCreate(StrictModel):
    text: str = Field(min_length=1, max_length=100_000)
    workspace_id: str | None = None
    persona_id: str | None = None
    model: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    model_settings: ModelGenerationSettings = Field(default_factory=ModelGenerationSettings)


class ProviderCheck(StrictModel):
    provider: str
    settings: dict = Field(default_factory=dict)


class MediaJobCreate(StrictModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    chat_id: str | None = None
    provider: str | None = None
    model: str | None = None
    size: str | None = None
    quality: str | None = None
    seconds: str | None = None
    backend: str | None = None
    base_url: str | None = None


class MediaEditJobCreate(StrictModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    operation: Literal["image_to_image", "inpaint", "outpaint"]
    source_media_id: str
    mask_media_id: str | None = None
    chat_id: str | None = None
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    required_features: list[str] = Field(default_factory=list, max_length=64)


class SpeechSynthesisCreate(StrictModel):
    text: str
    chat_id: str | None = None
    persona_id: str | None = None
    format: str | None = None
    voice: str | None = None
    model: str | None = None
    speed: str | None = None
    instructions: str | None = None


class BackupCreate(StrictModel):
    include_media: bool = False


class BackupRepresentation(BaseModel):
    name: str
    size: int
    created_at: int
    created_at_iso: str
    include_media: bool | None = None
    download_url: str


class BackupListResponse(BaseModel):
    items: list[BackupRepresentation]


class ResourceControlAuthorizationUpdate(StrictModel):
    provider: Literal["ollama", "comfyui", "automatic1111"]
    exclusive_control: bool = False
    allow_release: bool = False


class ResourceCoordinationUpdate(StrictModel):
    mode: Literal["disabled", "observe", "managed"] = "disabled"
    reserve_vram_mb: int = Field(default=1024, ge=0, le=131072)
    max_wait_seconds: int = Field(default=300, ge=1, le=3600)
    poll_interval_seconds: float = Field(default=2.0, ge=0.25, le=60)
    authorizations: list[ResourceControlAuthorizationUpdate] = Field(default_factory=list, max_length=3)


TurnState = Literal["queued", "running", "completed", "failed", "cancelled"]
CapabilityState = Literal[
    "pending_confirmation",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "denied",
    "expired",
]


class ChatRepresentation(BaseModel):
    id: str
    workspace_id: str | None = None
    persona_id: str | None = None
    model_override: str | None = None
    memory_mode: str
    title: str | None = None
    hidden_in_ui: bool
    created_at: int
    updated_at: int


class MessageRepresentation(BaseModel):
    id: str
    role: str
    text: str
    created_at: int


class ChatListResponse(BaseModel):
    items: list[ChatRepresentation]


class ChatDetailResponse(BaseModel):
    chat: ChatRepresentation
    messages: list[MessageRepresentation]


class TurnErrorRepresentation(BaseModel):
    code: str
    message: str


class TurnContextRepresentation(BaseModel):
    context_window_tokens: int | None = None
    prompt_budget_tokens: int | None = None
    prompt_tokens_estimated: int | None = None
    prompt_tokens_actual: int | None = None
    included_message_count: int | None = None
    omitted_message_count: int | None = None
    included_memory_count: int | None = None
    omitted_memory_count: int | None = None
    summary_id: str | None = None
    degraded_reason: str | None = None


class ConversationSummaryRepresentation(BaseModel):
    id: str
    through_message_id: str
    provider: str
    model: str
    prompt_version: str
    content: str
    estimated_tokens: int
    created_at: int


class LatestTurnContextRepresentation(TurnContextRepresentation):
    turn_id: str


class ChatContextResponse(BaseModel):
    chat_id: str
    memory_mode: str
    summary: ConversationSummaryRepresentation | None = None
    latest_turn_context: LatestTurnContextRepresentation | None = None


class TurnRepresentation(BaseModel):
    id: str
    chat_id: str
    job_id: str | None = None
    status: TurnState
    provider: str
    model: str
    user_message_id: str
    assistant_message_id: str | None = None
    accumulated_text: str = ""
    error: TurnErrorRepresentation | None = None
    created_at: int
    started_at: int | None = None
    completed_at: int | None = None
    context: TurnContextRepresentation | None = None


class JobRepresentation(BaseModel):
    id: str
    kind: str
    status: TurnState
    chat_id: str | None = None
    turn_id: str | None = None
    capability_request_id: str | None = None
    progress: str = ""
    queue_position: int | None = None
    result: dict | None = None
    error: str = ""
    cancel_requested: bool = False
    created_at: int | None = None
    started_at: int | None = None
    completed_at: int | None = None


class TurnAcceptedResponse(BaseModel):
    turn: TurnRepresentation
    job: JobRepresentation


class MediaJobAcceptedResponse(BaseModel):
    job_id: str
    capability_request_id: str
    chat_id: str | None = None
    status: TurnState


class MediaGenerationAttemptRepresentation(BaseModel):
    id: str
    media_plan_id: str
    attempt_number: int
    operation: Literal["generate", "inpaint", "outpaint", "image_to_image"]
    status: Literal["running", "passed", "failed", "unverified", "error", "cancelled"]
    media_id: str | None = None
    media_url: str | None = None
    validation_id: str | None = None
    source_media_id: str | None = None
    workflow_resource_id: str | None = None
    score: float | None = None
    threshold: float | None = None
    error: dict | None = None
    started_at: int
    completed_at: int | None = None


class MediaGenerationAttemptListResponse(BaseModel):
    items: list[MediaGenerationAttemptRepresentation]


class CapabilityDefinitionRepresentation(BaseModel):
    key: str
    tool_name: str
    title: str
    description: str
    permission_mode: Literal["confirm", "explicit"]
    available: bool


class CapabilityDefinitionListResponse(BaseModel):
    items: list[CapabilityDefinitionRepresentation]


class MediaCatalogSettingsUpdate(StrictModel):
    vram_budget_mb: int = Field(ge=0, le=131072)
    max_loras: int = Field(ge=0, le=8)


class MediaCatalogSettingsRepresentation(BaseModel):
    vram_budget_mb: int
    max_loras: int


class MediaCatalogResourceWrite(StrictModel):
    resource_type: Literal["model", "lora", "workflow"]
    kind: Literal["image", "video"]
    name: str = Field(min_length=1, max_length=160)
    provider_key: Literal["openai-image", "local-image", "openai-video"]
    backend: Literal["openai", "automatic1111", "comfyui"]
    external_id: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    operations: list[Literal["generate", "inpaint", "outpaint", "image_to_image"]] = Field(
        default_factory=lambda: ["generate"], min_length=1, max_length=4
    )
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    features: list[str] = Field(default_factory=list, max_length=64)
    estimated_vram_mb: int = Field(default=0, ge=0, le=131072)
    estimated_load_seconds: float = Field(default=0, ge=0, le=3600)
    default_settings: dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=4000)
    compatible_model_ids: list[str] = Field(default_factory=list, max_length=100)


class MediaCatalogResourceRepresentation(MediaCatalogResourceWrite):
    id: str
    revision: int
    created_at: int
    updated_at: int


class MediaPlanningVocabularyRepresentation(BaseModel):
    operations: list[str]
    domains: list[str]
    content_tags: list[str]
    features: list[str]


class MediaCatalogRepresentation(BaseModel):
    settings: MediaCatalogSettingsRepresentation
    resources: list[MediaCatalogResourceRepresentation]
    vocabulary: MediaPlanningVocabularyRepresentation


class MediaPlanRequirementsCreate(StrictModel):
    kind: Literal["image", "video"]
    operation: Literal["generate", "inpaint", "outpaint", "image_to_image"] = "generate"
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    required_features: list[str] = Field(default_factory=list, max_length=64)


class MediaPlanResourceSnapshot(BaseModel):
    id: str
    resource_type: Literal["model", "lora", "workflow"]
    name: str
    provider_key: str
    backend: str
    external_id: str
    domains: list[str]
    content_tags: list[str]
    features: list[str]
    estimated_vram_mb: int
    default_settings: dict
    updated_at: int
    revision: int


class MediaPlanSelectionExplanation(BaseModel):
    resource_id: str
    role: str
    name: str
    reason: str


class MediaPlanRejectionExplanation(BaseModel):
    resource_id: str
    name: str
    reasons: list[str]


class MediaPlanExplanation(BaseModel):
    summary: str
    selected: list[MediaPlanSelectionExplanation]
    warnings: list[str]
    rejected: list[MediaPlanRejectionExplanation]


class MediaPlanBlock(BaseModel):
    code: str
    message: str


class MediaIdentityConditioningRepresentation(BaseModel):
    required: bool
    status: Literal["ready", "blocked", "conditioned"]
    mode: str | None = None
    persona_id: str | None = None
    profile_id: str | None = None
    profile_revision: int | None = None
    reference_id: str | None = None
    reference_sha256: str | None = None
    workflow_resource_id: str | None = None
    appearance_description_included: bool = False
    verification_status: Literal["not_evaluated"] = "not_evaluated"
    claim_status: Literal["unverified"] | None = None


class MediaPlanRepresentation(BaseModel):
    id: str | None = None
    source: Literal["coordinator", "manual"]
    status: Literal["ready", "blocked"]
    kind: Literal["image", "video"]
    operation: str
    requirements: dict
    selected_resources: list[MediaPlanResourceSnapshot]
    explanation: MediaPlanExplanation
    estimated_vram_mb: int
    identity_conditioning: MediaIdentityConditioningRepresentation | None = None
    block: MediaPlanBlock | None = None
    created_at: int | None = None


class CapabilityRequestRepresentation(BaseModel):
    id: str
    capability_key: str
    status: CapabilityState
    permission_mode: Literal["confirm", "explicit"]
    arguments: dict
    result: dict | None = None
    error: TurnErrorRepresentation | None = None
    chat_id: str | None = None
    turn_id: str | None = None
    assistant_message_id: str | None = None
    job_id: str | None = None
    requested_at: int
    decided_at: int | None = None
    started_at: int | None = None
    completed_at: int | None = None
    expires_at: int | None = None
    media_plan: MediaPlanRepresentation | None = None


class CapabilityRequestListResponse(BaseModel):
    items: list[CapabilityRequestRepresentation]


class CapabilityEventRepresentation(BaseModel):
    id: str
    capability_request_id: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    detail: dict
    created_at: int


class CapabilityHistoryResponse(BaseModel):
    request: CapabilityRequestRepresentation
    events: list[CapabilityEventRepresentation]


class TaskModelProfileUpdate(StrictModel):
    enabled: bool
    provider: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=240)
    fallback_provider: str | None = Field(default=None, max_length=80)
    fallback_model: str | None = Field(default=None, max_length=240)
    max_input_tokens: int = Field(ge=128, le=262144)
    max_output_tokens: int = Field(ge=16, le=8192)
    timeout_seconds: float = Field(ge=1, le=600)
    temperature: float = Field(ge=0, le=2)
    fallback_policy: Literal["deterministic", "skip", "fail"]


class TaskModelProfileRepresentation(BaseModel):
    role: str
    title: str
    description: str
    enabled: bool
    provider: str
    model: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    temperature: float
    fallback_policy: Literal["deterministic", "skip", "fail"]
    updated_at: int


class TaskModelProfileListResponse(BaseModel):
    items: list[TaskModelProfileRepresentation]


class TaskModelReadinessRepresentation(BaseModel):
    role: str
    ready: bool
    status: str
    message: str
    primary_ready: bool
    fallback_ready: bool
    effective_model: str | None = None
    fallback_effective_model: str | None = None


class TaskModelRunRepresentation(BaseModel):
    id: str
    role: str
    chat_id: str | None = None
    turn_id: str | None = None
    requested_provider: str | None = None
    requested_model: str | None = None
    executed_provider: str | None = None
    executed_model: str | None = None
    status: Literal["running", "completed", "fallback", "failed"]
    fallback_used: bool
    error: TurnErrorRepresentation | None = None
    attempts: list[dict]
    input_tokens_estimated: int
    output_tokens_estimated: int | None = None
    latency_ms: int | None = None
    started_at: int
    completed_at: int | None = None


class TaskModelRunListResponse(BaseModel):
    items: list[TaskModelRunRepresentation]


class ModelListResponse(BaseModel):
    models: list[str]


def services(request: Request) -> ApplicationServices:
    return request.app.state.services


def current_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthContext:
    return services(request).resources.authenticate(session_token)


def _set_session_cookie(response: Response, context: AuthContext, ttl_seconds: int, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        context.token,
        httponly=True,
        samesite="strict",
        max_age=ttl_seconds,
        path="/",
        secure=secure,
    )


@router.get("/health", tags=["system"])
def health():
    return {"ok": True}


@router.post("/users", tags=["session"])
def create_user(body: Credentials, request: Request):
    return services(request).resources.create_user(body.username, body.password)


@router.post("/session", tags=["session"])
def login(body: Credentials, request: Request, response: Response):
    app_services = services(request)
    config = app_services.runtime.config
    throttle_key = app_services.login_throttle.key(
        request_client_address(request, trust_proxy_headers=config.trust_proxy_headers),
        body.username,
    )
    app_services.login_throttle.check(throttle_key)
    try:
        context, payload = app_services.resources.login(body.username, body.password)
    except AuthenticationError:
        app_services.login_throttle.failure(throttle_key)
        raise
    app_services.login_throttle.success(throttle_key)
    _set_session_cookie(response, context, config.session_ttl_seconds, secure=config.secure_cookies)
    return payload


@router.get("/session", tags=["session"])
def session(request: Request, context: AuthContext = Depends(current_user)):
    return {
        "user_id": context.user_id,
        "expires_at": context.expires_at,
        "ttl_seconds": services(request).runtime.config.session_ttl_seconds,
        "is_admin": context.is_admin,
    }


@router.delete("/session", tags=["session"])
def logout(response: Response, request: Request, context: AuthContext = Depends(current_user)):
    services(request).resources.logout(context.token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/settings", tags=["settings"])
def get_settings(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).resources.get_settings(context.user_id)


@router.put("/settings", tags=["settings"])
def update_settings(body: SettingsUpdate, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).resources.save_settings(context.user_id, body.model_dump())


@router.get("/workspaces", tags=["workspaces"])
def list_workspaces(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).resources.list_workspaces(context.user_id)}


@router.post("/workspaces", tags=["workspaces"])
def create_workspace(body: WorkspaceWrite, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).resources.create_workspace(context.user_id, body.name)


@router.get("/workspaces/{workspace_id}", tags=["workspaces"])
def get_workspace(workspace_id: str, request: Request, context: AuthContext = Depends(current_user)):
    items = services(request).resources.list_workspaces(context.user_id)
    item = next((value for value in items if value["id"] == workspace_id), None)
    if not item:
        raise NotFoundError("workspace not found")
    return item


@router.put("/workspaces/{workspace_id}", tags=["workspaces"])
def update_workspace(
    workspace_id: str,
    body: WorkspaceWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.update_workspace(context.user_id, workspace_id, body.name)


@router.delete("/workspaces/{workspace_id}", tags=["workspaces"])
def delete_workspace(workspace_id: str, request: Request, context: AuthContext = Depends(current_user)):
    services(request).resources.delete_workspace(context.user_id, workspace_id)
    return {"ok": True}


@router.get("/personas", tags=["personas"])
def list_personas(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).resources.list_personas(context.user_id)}


@router.post("/personas", tags=["personas"])
def create_persona(body: PersonaWrite, request: Request, context: AuthContext = Depends(current_user)):
    values = body.model_dump(exclude_none=True)
    values["workspace_ids"] = body.workspace_ids or [body.workspace_id]
    return services(request).resources.save_persona(context.user_id, values)


@router.get("/personas/{persona_id}", tags=["personas"])
def get_persona(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).resources.get_persona(context.user_id, persona_id)


@router.put("/personas/{persona_id}", tags=["personas"])
def update_persona(
    persona_id: str,
    body: PersonaWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    values = body.model_dump(exclude_none=True)
    values["workspace_ids"] = body.workspace_ids or [body.workspace_id]
    return services(request).resources.save_persona(context.user_id, values, persona_id)


@router.delete("/personas/{persona_id}", tags=["personas"])
def delete_persona(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    services(request).resources.delete_persona(context.user_id, persona_id)
    return {"ok": True}


@router.get("/memories", tags=["memories"], response_model=MemoryListResponse)
def list_memories(
    request: Request,
    scope: str | None = Query(default=None, pattern="^(global|workspace|persona|chat)$"),
    scope_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern="^(pending|active|rejected|forgotten|superseded)(,(pending|active|rejected|forgotten|superseded))*$",
    ),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).memory.list(context.user_id, scope, scope_id, status)}


@router.post("/memories", tags=["memories"], response_model=MemoryRepresentation)
def create_memory(body: MemoryCreate, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.create(context.user_id, body.model_dump())


@router.put("/memories/{memory_id}", tags=["memories"], response_model=MemoryRepresentation)
def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).memory.revise(
        context.user_id,
        memory_id,
        body.model_dump(exclude_unset=True),
    )


@router.delete("/memories/{memory_id}", tags=["memories"])
def delete_memory(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.delete(context.user_id, memory_id)


@router.post("/memories/bulk-actions", tags=["memories"], response_model=BulkActionRepresentation)
def bulk_memory_action(body: MemoryBulkAction, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.bulk_action(context.user_id, body.action, body.ids)


@router.post("/memories/{memory_id}/approve", tags=["memories"], response_model=MemoryRepresentation)
def approve_memory(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.approve(context.user_id, memory_id)


@router.post("/memories/{memory_id}/reject", tags=["memories"], response_model=MemoryRepresentation)
def reject_memory(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.reject(context.user_id, memory_id)


@router.post("/memories/{memory_id}/forget", tags=["memories"], response_model=MemoryRepresentation)
def forget_memory(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.forget(context.user_id, memory_id)


@router.post("/memories/{memory_id}/undo", tags=["memories"], response_model=MemoryRepresentation)
def undo_memory(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.undo(context.user_id, memory_id)


@router.get("/memories/{memory_id}/history", tags=["memories"], response_model=MemoryHistoryResponse)
def memory_history(memory_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).memory.history(context.user_id, memory_id)


@router.get("/chats", response_model=ChatListResponse, tags=["chats"])
def list_chats(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).conversations.list_chats(context.user_id)}


@router.post("/chats", response_model=ChatRepresentation, tags=["chats"])
def create_chat(body: ChatCreate, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).conversations.create_chat(context.user_id, body.model_dump())


@router.get("/chats/{chat_id}", response_model=ChatDetailResponse, tags=["chats"])
def get_chat(chat_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).conversations.get_chat(context.user_id, chat_id)
    if not value:
        raise NotFoundError("chat not found")
    return value


@router.put("/chats/{chat_id}", response_model=ChatRepresentation, tags=["chats"])
def update_chat(
    chat_id: str,
    body: ChatUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    value = services(request).conversations.update_chat(
        context.user_id,
        chat_id,
        body.model_dump(exclude_unset=True),
    )
    if not value:
        raise NotFoundError("chat not found")
    return value


@router.delete("/chats/{chat_id}", tags=["chats"])
def delete_chat(chat_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).conversations.delete_chat(context.user_id, chat_id):
        raise NotFoundError("chat not found")
    return {"ok": True, "id": chat_id, "deleted": True}


@router.post("/chats/{chat_id}/hide", tags=["chats"])
def hide_chat(chat_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).conversations.hide_chat(context.user_id, chat_id):
        raise NotFoundError("chat not found")
    return {"ok": True, "id": chat_id, "hidden": True}


@router.post("/chats/bulk-actions", tags=["chats"], response_model=BulkActionRepresentation)
def bulk_chat_action(body: ChatBulkAction, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).conversations.bulk_chat_action(context.user_id, body.action, body.ids)


@router.post(
    "/chats/{chat_id}/turns",
    response_model=TurnAcceptedResponse,
    status_code=202,
    tags=["turns"],
)
def create_turn(
    chat_id: str,
    body: TurnCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    turn, job = services(request).conversations.create_turn(
        context.user_id,
        chat_id,
        body.model_dump(),
    )
    return {"turn": turn, "job": job}


@router.get("/chats/{chat_id}/context", tags=["chats"], response_model=ChatContextResponse)
def chat_context(chat_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).conversations.context_detail(context.user_id, chat_id)
    if not value:
        raise NotFoundError("chat not found")
    return value


@router.get("/turns/{turn_id}", response_model=TurnRepresentation, tags=["turns"])
def get_turn(turn_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).conversations.get_turn(context.user_id, turn_id)
    if not value:
        raise NotFoundError("turn not found")
    return value


@router.get("/turns/{turn_id}/events", tags=["turns"])
def turn_events(
    turn_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: AuthContext = Depends(current_user),
):
    app_services = services(request)
    snapshot = app_services.conversations.get_turn(context.user_id, turn_id)
    if not snapshot:
        raise NotFoundError("turn not found")
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None

    def stream():
        for event in app_services.broker.subscribe(turn_id, snapshot, cursor):
            if event is None:
                yield ": heartbeat\n\n"
                continue
            yield f"id: {event.sequence}\nevent: {event.event}\ndata: {json.dumps(event.data, separators=(',', ':'), default=str)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}", response_model=JobRepresentation, tags=["jobs"])
def get_job(job_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).jobs.get(context.user_id, job_id)
    if not value:
        raise NotFoundError()
    return value


@router.delete("/jobs/{job_id}", response_model=JobRepresentation, tags=["jobs"])
def cancel_job(job_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).jobs.cancel(context.user_id, job_id)
    if not value:
        raise NotFoundError()
    return value


@router.get(
    "/capabilities",
    response_model=CapabilityDefinitionListResponse,
    tags=["capabilities"],
)
def capabilities(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).capabilities.definitions(context.user_id)}


@router.get(
    "/capability-requests",
    response_model=CapabilityRequestListResponse,
    tags=["capabilities"],
)
def capability_requests(
    request: Request,
    chat_id: str | None = None,
    status: list[CapabilityState] | None = Query(default=None),
    context: AuthContext = Depends(current_user),
):
    return {
        "items": services(request).capabilities.list_requests(
            context.user_id,
            chat_id=chat_id,
            statuses=set(status or []),
        )
    }


@router.get(
    "/capability-requests/{request_id}",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def capability_request(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.get(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


@router.get(
    "/capability-requests/{request_id}/events",
    response_model=CapabilityHistoryResponse,
    tags=["capabilities"],
)
def capability_events(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    capability = services(request).capabilities.get(context.user_id, request_id)
    events = services(request).capabilities.events(context.user_id, request_id)
    if not capability or events is None:
        raise NotFoundError("capability request not found")
    return {"request": capability, "events": events}


@router.post(
    "/capability-requests/{request_id}/approval",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def approve_capability(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.approve(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


@router.post(
    "/capability-requests/{request_id}/denial",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def deny_capability(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.deny(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


@router.delete(
    "/capability-requests/{request_id}",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def cancel_capability(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.cancel(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


@router.get(
    "/media-catalog",
    response_model=MediaCatalogRepresentation,
    tags=["media-catalog"],
)
def media_catalog(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.catalog(context.user_id)


@router.put(
    "/media-catalog/settings",
    response_model=MediaCatalogSettingsRepresentation,
    tags=["media-catalog"],
)
def update_media_catalog_settings(
    body: MediaCatalogSettingsUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.update_settings(context.user_id, body.model_dump())


@router.post(
    "/media-catalog/resources",
    response_model=MediaCatalogResourceRepresentation,
    status_code=201,
    tags=["media-catalog"],
)
def create_media_catalog_resource(
    body: MediaCatalogResourceWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.create_resource(context.user_id, body.model_dump())


@router.get(
    "/media-catalog/resources/{resource_id}",
    response_model=MediaCatalogResourceRepresentation,
    tags=["media-catalog"],
)
def media_catalog_resource(resource_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).media_catalog.resource(context.user_id, resource_id)
    if not value:
        raise NotFoundError("media catalog resource not found")
    return value


@router.put(
    "/media-catalog/resources/{resource_id}",
    response_model=MediaCatalogResourceRepresentation,
    tags=["media-catalog"],
)
def update_media_catalog_resource(
    resource_id: str,
    body: MediaCatalogResourceWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.update_resource(context.user_id, resource_id, body.model_dump())


@router.delete(
    "/media-catalog/resources/{resource_id}",
    tags=["media-catalog"],
)
def delete_media_catalog_resource(
    resource_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    if not services(request).media_catalog.delete_resource(context.user_id, resource_id):
        raise NotFoundError("media catalog resource not found")
    return {"ok": True}


@router.post(
    "/media-catalog/plan-previews",
    response_model=MediaPlanRepresentation,
    tags=["media-catalog"],
)
def preview_media_plan(
    body: MediaPlanRequirementsCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.preview(context.user_id, body.model_dump())


@router.get(
    "/media-plans/{plan_id}",
    response_model=MediaPlanRepresentation,
    tags=["media-catalog"],
)
def media_plan(plan_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).media_catalog.plan(context.user_id, plan_id)
    if not value:
        raise NotFoundError("media plan not found")
    return value


@router.get(
    "/media-plans/{plan_id}/attempts",
    response_model=MediaGenerationAttemptListResponse,
    tags=["media-catalog"],
)
def media_plan_attempts(plan_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).media_catalog.attempts(context.user_id, plan_id)}


@router.get(
    "/task-models",
    response_model=TaskModelProfileListResponse,
    tags=["task-models"],
)
def task_models(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).task_models.profiles(context.user_id)}


@router.put(
    "/task-models/{role}",
    response_model=TaskModelProfileRepresentation,
    tags=["task-models"],
)
def update_task_model(
    role: str,
    body: TaskModelProfileUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).task_models.update_profile(
        context.user_id,
        role,
        body.model_dump(),
    )


@router.post(
    "/task-models/{role}/check",
    response_model=TaskModelReadinessRepresentation,
    tags=["task-models"],
)
def check_task_model(role: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).task_models.readiness(context.user_id, role)


@router.get(
    "/task-model-runs",
    response_model=TaskModelRunListResponse,
    tags=["task-models"],
)
def task_model_runs(
    request: Request,
    role: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).task_models.runs(context.user_id, role=role, limit=limit)}


@router.get("/models", response_model=ModelListResponse, tags=["providers"])
def models(request: Request, _context: AuthContext = Depends(current_user)):
    return {"models": services(request).provider_service.models()}


@router.post("/provider-checks", tags=["providers"])
def provider_check(body: ProviderCheck, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).provider_service.check(context.user_id, body.provider, body.settings)
    if value is None:
        raise NotFoundError("unknown provider")
    return value


@router.post(
    "/media/image-jobs",
    response_model=MediaJobAcceptedResponse,
    status_code=202,
    tags=["media"],
)
def image_job(
    body: MediaJobCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
    context: AuthContext = Depends(current_user),
):
    value = services(request).capabilities.start_explicit(
        "image",
        context.user_id,
        body.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
    )
    return {
        "job_id": value["job_id"],
        "capability_request_id": value["id"],
        "chat_id": value["chat_id"],
        "status": value["status"],
    }


@router.post(
    "/media/image-edit-jobs",
    response_model=MediaJobAcceptedResponse,
    status_code=202,
    tags=["media"],
)
def image_edit_job(
    body: MediaEditJobCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
    context: AuthContext = Depends(current_user),
):
    value = services(request).capabilities.start_edit(
        context.user_id,
        body.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
    )
    return {
        "job_id": value["job_id"],
        "capability_request_id": value["id"],
        "chat_id": value["chat_id"],
        "status": value["status"],
    }


@router.post(
    "/media/video-jobs",
    response_model=MediaJobAcceptedResponse,
    status_code=202,
    tags=["media"],
)
def video_job(
    body: MediaJobCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
    context: AuthContext = Depends(current_user),
):
    value = services(request).capabilities.start_explicit(
        "video",
        context.user_id,
        body.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
    )
    return {
        "job_id": value["job_id"],
        "capability_request_id": value["id"],
        "chat_id": value["chat_id"],
        "status": value["status"],
    }


@router.get("/speech/voices", tags=["speech"])
def voices(
    request: Request,
    base_url: str | None = None,
    context: AuthContext = Depends(current_user),
):
    return {"voices": services(request).speech.voices(context.user_id, base_url)}


@router.post("/speech/syntheses", tags=["speech"])
def synthesize(
    body: SpeechSynthesisCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    result = services(request).speech.synthesize(context.user_id, body.model_dump(exclude_none=True))
    return {
        "audio_id": result["audio_id"],
        "audio_url": f"/api/v1/audio/{result['audio_id']}",
        "format": result["format"],
    }


@router.post("/speech/transcriptions", tags=["speech"])
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    context: AuthContext = Depends(current_user),
):
    content = await file.read()
    return services(request).speech.transcribe(context.user_id, file.filename or "audio.webm", content)


@router.get("/media/{media_id}", tags=["media"])
def media_file(media_id: str, request: Request, context: AuthContext = Depends(current_user)):
    path = services(request).resources.media_path(context.user_id, media_id)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/audio/{audio_id}", tags=["speech"])
def audio_file(audio_id: str, request: Request, context: AuthContext = Depends(current_user)):
    path = services(request).resources.audio_path(context.user_id, audio_id)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/admin/backups", tags=["admin"], response_model=BackupListResponse)
def list_backups(request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    return {"items": app_services.operations.list_backups()}


@router.post("/admin/backups", tags=["admin"], response_model=BackupRepresentation)
def create_backup(body: BackupCreate, request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    return app_services.operations.create_backup(body.include_media)


@router.get("/admin/backups/{name}/download", tags=["admin"])
def download_backup(name: str, request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    path = app_services.operations.backup_path(name)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.delete("/admin/backups/{name}", tags=["admin"])
def delete_backup(name: str, request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    app_services.operations.delete_backup(name)
    return {"ok": True}


@router.post("/admin/backups/{name}/verify", tags=["admin"])
def verify_backup(name: str, request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    try:
        return app_services.operations.verify_backup(name)
    except Exception as exc:  # noqa: BLE001 - all verifier failures become one safe operator-facing result
        app_services.runtime.logger.warning("backup verification failed error=%s", exc.__class__.__name__)
        raise RequestError("Backup verification failed. The snapshot is unsafe or corrupt.", 409) from exc


@router.get("/admin/observability", tags=["admin"])
def observability(request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    value = app_services.runtime.metrics.snapshot()
    value["queues"] = app_services.jobs.operational_snapshot()
    value["storage"] = app_services.operations.storage_report()
    value["readiness"] = app_services.operations.readiness()
    return value


@router.get("/admin/resource-coordination", tags=["admin"])
def resource_coordination(request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    return app_services.resource_coordination.status(context.user_id)


@router.put("/admin/resource-coordination", tags=["admin"])
def update_resource_coordination(
    body: ResourceCoordinationUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    app_services = services(request)
    app_services.resources.require_admin(context)
    values = body.model_dump()
    values["authorizations"] = [item.model_dump() for item in body.authorizations]
    return app_services.resource_coordination.update(context.user_id, values)


@router.post("/admin/resource-coordination/check", tags=["admin"])
def check_resource_coordination(request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    return app_services.resource_coordination.status(context.user_id)


@router.get("/admin/resource-coordination/events", tags=["admin"])
def resource_coordination_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    context: AuthContext = Depends(current_user),
):
    app_services = services(request)
    app_services.resources.require_admin(context)
    return {"items": app_services.resource_coordination.events(limit)}


@router.post("/diagnostics/client-events", tags=["diagnostics"])
async def client_event(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    token = request.cookies.get(SESSION_COOKIE)
    user_id = None
    if token:
        try:
            user_id = services(request).resources.authenticate(token).user_id
        except Exception:
            user_id = None
    services(request).operations.client_event(user_id, payload if isinstance(payload, dict) else {})
    return {"ok": True}


@router.get("/admin/diagnostics/log", tags=["admin"])
def diagnostic_log(request: Request, context: AuthContext = Depends(current_user)):
    app_services = services(request)
    app_services.resources.require_admin(context)
    filename, content = app_services.operations.diagnostic_log(context.user_id)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
