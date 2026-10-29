from __future__ import annotations

import asyncio
import json
import mimetypes
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.application import ApplicationServices
from app.data_locality import conversation_locality, leaves_this_machine
from app.provider_contracts import CancellationToken
from app.resource_service import AuthContext
from app.runtime import SESSION_COOKIE
from app.session_cookie import set_session_cookie
from app.security import request_client_address
from app.service_errors import AuthenticationError, NotFoundError, RequestError
from app.speech_clients import SpeechCancelled
from app.model_setup import setup_models
from app.workflow_template import resolve_template
from app.api_models import (
    StrictModel,
    Credentials,
    SettingsUpdate,
    WorkspaceWrite,
    PersonaWrite,
    PersonaCardWrite,
    PersonaLoreWrite,
    PersonaLoreCopy,
    PersonaLorePreview,
    MemoryCreate,
    MemoryProposalCreate,
    MemoryUpdate,
    MemoryBulkAction,
    ChatBulkAction,
    BulkActionRepresentation,
    MemoryRepresentation,
    MemoryListResponse,
    MemoryHistoryResponse,
    ChatCreate,
    ChatUpdate,
    TurnCreate,
    TurnAgain,
    ProviderCheck,
    ComfyUIWorkflowInspection,
    CheckpointDiscovery,
    ModelSetupRequest,
    ModelsFromCheckpoints,
    ModelPrefill,
    CivitaiLookup,
    ComfyUIWorkflowInspectionRepresentation,
    MediaJobCreate,
    MediaEditJobCreate,
    SpeechSynthesisCreate,
    SpeechSessionCreate,
    SpeechPieceCreate,
    BackupCreate,
    BackupRepresentation,
    BackupListResponse,
    ResourceCoordinationUpdate,
    CapabilityState,
    ChatRepresentation,
    ChatListResponse,
    ChatDetailResponse,
    ChatContextResponse,
    TurnRepresentation,
    JobRepresentation,
    TurnAcceptedResponse,
    MediaJobAcceptedResponse,
    MediaGenerationAttemptListResponse,
    MediaJournalRepresentation,
    MediaJournalListResponse,
    MediaLibraryListResponse,
    CapabilityDefinitionListResponse,
    MediaCatalogSettingsUpdate,
    MediaCatalogSettingsRepresentation,
    MediaCatalogResourceWrite,
    MediaCatalogResourceRepresentation,
    WorkflowTemplateListResponse,
    WorkflowTemplateInstall,
    SceneBacklogRepresentation,
    SceneBacklogListResponse,
    SceneBacklogCreate,
    PhotoSetRepresentation,
    PhotoSetListResponse,
    PhotoSetCreate,
    PhotoSetProductionResponse,
    PresetExportRepresentation,
    PresetImportPreviewRepresentation,
    PresetImportRequest,
    PresetImportResultRepresentation,
    PresetSignalListResponse,
    PregenerationReadinessRepresentation,
    SceneProposalRequest,
    SceneProposalResponse,
    SceneBacklogStateUpdate,
    LibraryEntryRepresentation,
    LibraryEntryListResponse,
    LibraryEntryCreate,
    StarterPresetListResponse,
    StarterPresetInstallResponse,
    RoutingPreviewCreate,
    RoutingPreviewRepresentation,
    MediaPresetWrite,
    MediaPresetRepresentation,
    MediaPresetListResponse,
    MediaCatalogRepresentation,
    MediaPlanRequirementsCreate,
    MediaPlanRepresentation,
    CapabilityRequestRepresentation,
    CapabilityRequestListResponse,
    CapabilityHistoryResponse,
    MediaReadinessResponse,
    TaskModelProfileUpdate,
    TaskModelProfileRepresentation,
    TaskModelProfileListResponse,
    TaskModelReadinessRepresentation,
    TaskModelRunListResponse,
    ModelListResponse,
)


router = APIRouter(prefix="/api/v1")


def services(request: Request) -> ApplicationServices:
    return request.app.state.services


def current_user(
    request: Request,
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthContext:
    app_services = services(request)
    context = app_services.resources.authenticate(session_token)
    set_session_cookie(
        response,
        context,
        app_services.runtime.config.session_ttl_seconds,
        secure=app_services.runtime.config.secure_cookies,
    )
    return context


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
        context, payload = app_services.resources.login(body.username, body.password, body.remember)
    except AuthenticationError:
        app_services.login_throttle.failure(throttle_key)
        raise
    app_services.login_throttle.success(throttle_key)
    set_session_cookie(response, context, config.session_ttl_seconds, secure=config.secure_cookies)
    return payload


@router.get("/session", tags=["session"])
def session(request: Request, context: AuthContext = Depends(current_user)):
    return {
        "user_id": context.user_id,
        "expires_at": context.expires_at,
        "ttl_seconds": services(request).runtime.config.session_ttl_seconds,
        "is_admin": context.is_admin,
        "persistent": context.persistent,
    }


@router.get("/signup", tags=["session"])
def signup_status(request: Request):
    """Whether an account can be created here, so sign-in offers only what works."""

    return services(request).resources.signup_status()


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


@router.put("/personas/{persona_id}/card", tags=["personas"])
def update_persona_card(
    persona_id: str,
    body: PersonaCardWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.save_persona_card(context.user_id, persona_id, body.model_dump())


@router.get("/personas/{persona_id}/avatar", tags=["personas"])
def persona_avatar(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    found = services(request).resources.persona_avatar_path(context.user_id, persona_id)
    # The URL carries the content digest as ?v=, so a changed picture is a new
    # URL and this copy can be cached hard instead of re-asked-for on every
    # page the face appears on.
    return FileResponse(found, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/personas/{persona_id}/lore", tags=["personas"])
def list_persona_lore(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).resources.list_persona_lore(context.user_id, persona_id)}


@router.post("/personas/{persona_id}/lore", tags=["personas"])
def create_persona_lore(
    persona_id: str,
    body: PersonaLoreWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.save_persona_lore(context.user_id, persona_id, body.model_dump())


@router.put("/personas/{persona_id}/lore/{entry_id}", tags=["personas"])
def update_persona_lore(
    persona_id: str,
    entry_id: str,
    body: PersonaLoreWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.save_persona_lore(context.user_id, persona_id, body.model_dump(), entry_id)


@router.delete("/personas/{persona_id}/lore/{entry_id}", tags=["personas"])
def delete_persona_lore(
    persona_id: str,
    entry_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    services(request).resources.delete_persona_lore(context.user_id, persona_id, entry_id)
    return {"ok": True}


@router.get("/personas/{persona_id}/lore/copyable", tags=["personas"])
def copyable_persona_lore(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return {"groups": services(request).resources.copyable_persona_lore(context.user_id, persona_id)}


@router.post("/personas/{persona_id}/lore/copies", tags=["personas"])
def copy_persona_lore(
    persona_id: str,
    body: PersonaLoreCopy,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.copy_persona_lore(context.user_id, persona_id, body.source_entry_id)


@router.post("/personas/{persona_id}/lore/preview", tags=["personas"])
def preview_persona_lore(
    persona_id: str,
    body: PersonaLorePreview,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).resources.preview_persona_lore(context.user_id, persona_id, body.text)


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


@router.post("/memory-proposals", tags=["memories"], response_model=MemoryRepresentation)
def create_memory_proposal(
    body: MemoryProposalCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).memory.propose(context.user_id, body.model_dump())


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


@router.post(
    "/chats/{chat_id}/messages/{message_id}/again",
    response_model=TurnAcceptedResponse,
    status_code=202,
    tags=["turns"],
)
def answer_again(
    chat_id: str,
    message_id: str,
    body: TurnAgain,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    """Answer the latest message again - as it was, or as it has been corrected to."""

    turn, job = services(request).conversations.again(context.user_id, chat_id, message_id, body.model_dump())
    return {"turn": turn, "job": job}


@router.get("/photos/{photo_id}", tags=["chats"], include_in_schema=False)
def message_photo(photo_id: str, request: Request, context: AuthContext = Depends(current_user)):
    row = services(request).conversations.photo(context.user_id, photo_id)
    if not row:
        raise NotFoundError("photo not found")
    # A photo never changes once sent, so a browser may keep it.
    return Response(content=row.data, media_type=row.mime_type, headers={"Cache-Control": "private, max-age=31536000"})


@router.delete("/chats/{chat_id}/messages/{message_id}", tags=["chats"])
def delete_message(chat_id: str, message_id: str, request: Request, context: AuthContext = Depends(current_user)):
    """Delete a message together with the reply it belongs with."""

    services(request).conversations.delete_exchange(context.user_id, chat_id, message_id)
    return {"ok": True}


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


@router.post(
    "/capability-requests/{request_id}/replan",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def replan_capability(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.replan(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


@router.post(
    "/capability-requests/{request_id}/retry",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def retry_capability(request_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).capabilities.retry(context.user_id, request_id)
    if not value:
        raise NotFoundError("capability request not found")
    return value


class CapabilityVariation(StrictModel):
    mode: Literal["again", "different_look"]


@router.post(
    "/capability-requests/{request_id}/variations",
    response_model=CapabilityRequestRepresentation,
    tags=["capabilities"],
)
def capability_variation(
    request_id: str,
    body: CapabilityVariation,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    value = services(request).capabilities.variation(context.user_id, request_id, body.mode)
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


@router.get(
    "/media/readiness",
    response_model=MediaReadinessResponse,
    tags=["media"],
)
def media_readiness(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).capabilities.media_readiness(context.user_id)


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


@router.get(
    "/media-catalog/workflow-templates",
    response_model=WorkflowTemplateListResponse,
    tags=["media-catalog"],
)
def list_workflow_templates(
    request: Request,
    model_id: str = Query(default="", max_length=64),
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.workflow_templates(context.user_id, model_id)


@router.post(
    "/media-catalog/workflow-templates/{template_id}/verify",
    response_model=ComfyUIWorkflowInspectionRepresentation,
    tags=["media-catalog"],
)
def verify_workflow_template(
    template_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    # Verification, not discovery: the bindings are already declared, so the
    # only open question is whether these nodes and named files are installed.
    template = resolve_template(template_id)
    # An identity graph must route the reference to an output; a video graph is
    # asked only whether its nodes and files exist and the prompt can land.
    return services(request).provider_service.inspect_comfyui_workflow(
        context.user_id, template["workflow"], require_identity=template["kind"] == "image"
    )


@router.post(
    "/media-catalog/workflow-templates/{template_id}/installations",
    response_model=MediaCatalogResourceRepresentation,
    status_code=201,
    tags=["media-catalog"],
)
def install_workflow_template(
    template_id: str,
    body: WorkflowTemplateInstall,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.install_workflow_template(context.user_id, template_id, body.model_dump())


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
    "/media-catalog/starter-presets",
    response_model=StarterPresetListResponse,
    tags=["media-catalog"],
)
def starter_presets(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.starter_presets(context.user_id)


@router.post(
    "/media-catalog/starter-presets/install",
    response_model=StarterPresetInstallResponse,
    tags=["media-catalog"],
)
def install_starter_presets(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.install_starter_presets(context.user_id)


@router.post(
    "/media-catalog/routing-previews",
    response_model=RoutingPreviewRepresentation,
    tags=["media-catalog"],
)
def preview_media_routing(
    body: RoutingPreviewCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    """Diagnostic. Expected to be removed once routing is demonstrably stable."""

    return services(request).capabilities.routing_preview(context.user_id, body.text, body.kind)


@router.get(
    "/media-catalog/presets",
    response_model=MediaPresetListResponse,
    tags=["media-catalog"],
)
def media_presets(
    request: Request,
    kind: Literal["image", "video"] | None = Query(default=None),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).media_catalog.presets(context.user_id, kind=kind)}


@router.post(
    "/media-catalog/presets",
    response_model=MediaPresetRepresentation,
    status_code=201,
    tags=["media-catalog"],
)
def create_media_preset(body: MediaPresetWrite, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.create_preset(context.user_id, body.model_dump())


@router.get(
    "/media-catalog/presets/{preset_id}",
    response_model=MediaPresetRepresentation,
    tags=["media-catalog"],
)
def media_preset(preset_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.preset(context.user_id, preset_id)


@router.put(
    "/media-catalog/presets/{preset_id}",
    response_model=MediaPresetRepresentation,
    tags=["media-catalog"],
)
def update_media_preset(
    preset_id: str,
    body: MediaPresetWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.update_preset(context.user_id, preset_id, body.model_dump())


@router.delete("/media-catalog/presets/{preset_id}", status_code=204, tags=["media-catalog"])
def delete_media_preset(preset_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).media_catalog.delete_preset(context.user_id, preset_id):
        raise NotFoundError("generation preset not found")
    return Response(status_code=204)


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
    provider_service = services(request).provider_service
    names = provider_service.models()
    # Which of them can be shown a photo, so the browser offers to send one
    # only where it would be seen.
    return {"models": names, "vision_models": provider_service.vision_models(names)}


@router.post("/provider-checks", tags=["providers"])
def provider_check(body: ProviderCheck, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).provider_service.check(context.user_id, body.provider, body.settings)
    if value is None:
        raise NotFoundError("unknown provider")
    return value


@router.post("/media-catalog/comfyui-checkpoints", tags=["media-catalog"])
def comfyui_checkpoints(
    body: CheckpointDiscovery,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    listing = services(request).provider_service.list_comfyui_checkpoints(context.user_id, body.settings)
    cataloged = services(request).media_catalog.cataloged_checkpoints(context.user_id)
    listing["checkpoints"] = [{"name": name, "cataloged": name in cataloged} for name in listing.get("checkpoints", [])]
    return listing


@router.post("/media-catalog/model-prefill", tags=["media-catalog"])
def comfyui_model_prefill(
    body: ModelPrefill,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).provider_service.comfyui_model_prefill(context.user_id, body.checkpoint, body.settings)


@router.post("/media-catalog/civitai-lookup", tags=["media-catalog"])
def civitai_lookup(
    body: CivitaiLookup,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).provider_service.civitai_model_lookup(body.checkpoint)


@router.post("/media-catalog/models/from-checkpoints", tags=["media-catalog"])
def models_from_checkpoints(
    body: ModelsFromCheckpoints,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_catalog.add_models_from_checkpoints(context.user_id, body.names)


@router.post("/media-catalog/models/setup", tags=["media-catalog"])
def setup_catalog_models(
    body: ModelSetupRequest,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return setup_models(
        services(request).media_catalog,
        services(request).provider_service,
        context.user_id,
        limit=body.limit,
        lookup=body.lookup,
        force=body.force,
    )


@router.post(
    "/media-catalog/identity-workflows/inspect",
    response_model=ComfyUIWorkflowInspectionRepresentation,
    tags=["media-catalog"],
)
def inspect_comfyui_identity_workflow(
    body: ComfyUIWorkflowInspection,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).provider_service.inspect_comfyui_workflow(
        context.user_id,
        body.workflow_patch,
        body.settings,
        require_identity=body.role == "identity",
    )


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


class DataLocalityPart(BaseModel):
    label: str
    provider: str
    locality: Literal["local", "cloud", "unknown", "off"]
    detail: str


class DataLocalityResponse(BaseModel):
    parts: list[DataLocalityPart]
    everything_local: bool


@router.get("/data-locality", response_model=DataLocalityResponse, tags=["settings"])
def data_locality(request: Request, context: AuthContext = Depends(current_user)):
    """Where each part of a conversation currently goes.

    One rule, computed on the server, so the browser cannot drift from it. Local
    and cloud are both legitimate choices here; what this exists to prevent is
    somebody not knowing which one they are using.
    """

    services_ = services(request)
    settings = services_.resources.get_settings(context.user_id)
    profiles = services_.task_models.profiles(context.user_id)
    # Roles may differ. Any cloud role makes the honest summary "cloud", because
    # a single background job sending conversation text off the machine is the
    # thing somebody would want to know about.
    task_provider = next(
        (profile["provider"] for profile in profiles if leaves_this_machine(profile.get("provider"))),
        profiles[0]["provider"] if profiles else "ollama",
    )
    return conversation_locality(settings, task_provider, services_.memory.semantic_recall_configured)


@router.get("/speech/voices", tags=["speech"])
def voices(
    request: Request,
    base_url: str | None = None,
    context: AuthContext = Depends(current_user),
):
    return {"voices": services(request).speech.voices(context.user_id, base_url)}


@router.post("/speech/syntheses", tags=["speech"])
async def synthesize(
    body: SpeechSynthesisCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    # Interrupting playback aborts this request, which cancels this handler.
    # Synthesis runs on a worker thread, so the cancellation trips a token the
    # provider read checks between pieces; otherwise the speech service keeps
    # generating audio nobody will hear and then writes a file nobody asked
    # for. Asking the connection whether it is still there would be the obvious
    # alternative and is not one: a request whose body has been read has no
    # pending message to inspect, so the answer is a guess. See ADR 0036.
    speech = services(request).speech
    values = body.model_dump(exclude_none=True)
    token = CancellationToken()
    try:
        result = await asyncio.to_thread(speech.synthesize, context.user_id, values, lambda: token.cancelled)
    except SpeechCancelled:
        # Nobody is on the other end of this response, so its shape does not
        # matter; what matters is that no artifact was written.
        return Response(status_code=204)
    finally:
        token.cancel()
    return {
        "audio_id": result["audio_id"],
        "audio_url": f"/api/v1/audio/{result['audio_id']}",
        "format": result["format"],
    }


AUDIO_MEDIA_TYPES = {"mp3": "audio/mpeg", "aac": "audio/aac"}
# The header that carries the id the finished audio will be stored under. It
# goes out before the first byte, so the browser can register the recording for
# replay while it is still listening to it.
AUDIO_ID_HEADER = "X-Nice-Assistant-Audio-Id"


async def _streamed_audio(pieces, token: CancellationToken):
    """Hand pieces to the browser, and stop when it stops listening.

    Each piece is pulled on a worker thread because the provider read is
    blocking. When the browser goes away this generator is closed, which trips
    the token; the provider read sees it at its next piece and stops, and the
    synthesis never reaches the line that stores it.
    """

    iterator = iter(pieces)
    try:
        while True:
            piece = await asyncio.to_thread(next, iterator, None)
            if piece is None:
                return
            yield piece
    finally:
        token.cancel()
        iterator.close()


@router.post("/speech/streams", tags=["speech"])
async def stream_speech(
    body: SpeechSynthesisCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    # Speech that starts when the first audio exists rather than when the last
    # one does. The completed file is still written at the end, so replay works
    # exactly as before; a stream the browser abandons writes nothing at all.
    # See ADR 0037.
    speech = services(request).speech
    values = body.model_dump(exclude_none=True)
    token = CancellationToken()
    audio_id, fmt, pieces = await asyncio.to_thread(
        speech.stream_synthesis, context.user_id, values, lambda: token.cancelled
    )
    return StreamingResponse(
        _streamed_audio(pieces, token),
        media_type=AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream"),
        headers={
            AUDIO_ID_HEADER: audio_id,
            # Nothing between here and the browser should hold this back
            # waiting for a complete body.
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/speech/sessions", tags=["speech"])
def begin_speech_session(
    body: SpeechSessionCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    # One reply, spoken a sentence at a time while it is still being
    # written, and stored once when it is finished. See ADR 0042.
    return services(request).speech.begin_speech_session(context.user_id, body.model_dump(exclude_none=True))


@router.post("/speech/sessions/{session_id}/pieces", tags=["speech"])
async def stream_speech_piece(
    session_id: str,
    body: SpeechPieceCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    speech = services(request).speech
    token = CancellationToken()
    fmt, pieces = await asyncio.to_thread(
        speech.stream_session_piece, context.user_id, session_id, body.text, lambda: token.cancelled
    )
    return StreamingResponse(
        _streamed_audio(pieces, token),
        media_type=AUDIO_MEDIA_TYPES.get(fmt, "application/octet-stream"),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/speech/sessions/{session_id}/finish", tags=["speech"])
def finish_speech_session(session_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).speech.finish_speech_session(context.user_id, session_id)


@router.delete("/speech/sessions/{session_id}", tags=["speech"])
def abandon_speech_session(session_id: str, request: Request, context: AuthContext = Depends(current_user)):
    services(request).speech.abandon_speech_session(context.user_id, session_id)
    return {"ok": True}


@router.post("/speech/transcriptions", tags=["speech"])
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    context: AuthContext = Depends(current_user),
):
    content = await file.read()
    # Conversion and the Whisper call take seconds. On the event loop they
    # would stall every other request for as long - including the reply
    # stream and the speech it is playing. See ADR 0043.
    return await asyncio.to_thread(
        services(request).speech.transcribe, context.user_id, file.filename or "audio.webm", content
    )


@router.get("/media", response_model=MediaLibraryListResponse, tags=["media"])
def media_library(
    request: Request,
    kind: Literal["image", "video"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).resources.list_media(context.user_id, kind=kind, limit=limit)}


@router.get("/media/{media_id}", tags=["media"])
def media_file(media_id: str, request: Request, context: AuthContext = Depends(current_user)):
    path = services(request).resources.media_path(context.user_id, media_id)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get(
    "/media-catalog/presets/{preset_id}/export",
    response_model=PresetExportRepresentation,
    tags=["media"],
)
def export_preset(preset_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.export_preset(context.user_id, preset_id)


@router.post(
    "/media-catalog/presets/import/preview",
    response_model=PresetImportPreviewRepresentation,
    tags=["media"],
)
def preview_preset_import(body: PresetImportRequest, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.preview_import(context.user_id, body.bundle)


@router.post(
    "/media-catalog/presets/import",
    response_model=PresetImportResultRepresentation,
    tags=["media"],
)
def import_presets(body: PresetImportRequest, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_catalog.import_bundle(context.user_id, body.bundle)


@router.get("/preset-signals", response_model=PresetSignalListResponse, tags=["media"])
def list_preset_signals(request: Request, context: AuthContext = Depends(current_user)):
    return {"items": services(request).media_catalog.preset_signals(context.user_id)}


@router.delete("/preset-signals/{preset_id}", status_code=204, tags=["media"])
def clear_preset_signals(preset_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).media_catalog.clear_preset_signals(context.user_id, preset_id):
        raise NotFoundError("no counts recorded for that preset")
    return Response(status_code=204)


@router.get("/photo-sets", response_model=PhotoSetListResponse, tags=["media"])
def list_photo_sets(
    request: Request,
    persona_id: str | None = None,
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).photo_sets.sets(context.user_id, persona_id=persona_id)}


@router.post("/photo-sets", response_model=PhotoSetRepresentation, status_code=201, tags=["media"])
def create_photo_set(body: PhotoSetCreate, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).photo_sets.create(
        context.user_id,
        persona_id=body.persona_id,
        scene=body.scene,
        variations=body.variations,
    )


@router.get("/photo-sets/{set_id}", response_model=PhotoSetRepresentation, tags=["media"])
def get_photo_set(set_id: str, request: Request, context: AuthContext = Depends(current_user)):
    value = services(request).photo_sets.get(context.user_id, set_id)
    if not value:
        raise NotFoundError("photo set not found")
    return value


@router.post(
    "/photo-sets/{set_id}/production",
    response_model=PhotoSetProductionResponse,
    tags=["media"],
)
def produce_photo_set(set_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).photo_sets.produce(context.user_id, set_id)


@router.delete("/photo-sets/{set_id}", status_code=204, tags=["media"])
def delete_photo_set(set_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).photo_sets.remove(context.user_id, set_id):
        raise NotFoundError("photo set not found")
    return Response(status_code=204)


@router.get("/scene-backlog", response_model=SceneBacklogListResponse, tags=["media"])
def scene_backlog(
    request: Request,
    persona_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).scene_backlog.entries(context.user_id, persona_id=persona_id, state=state)}


@router.post("/scene-backlog", response_model=SceneBacklogRepresentation, status_code=201, tags=["media"])
def propose_scene(body: SceneBacklogCreate, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).scene_backlog.propose(
        context.user_id,
        persona_id=body.persona_id,
        scene=body.scene,
        source="operator",
        source_detail=body.source_detail,
    )


@router.get(
    "/scene-backlog/production-readiness",
    response_model=PregenerationReadinessRepresentation,
    tags=["media"],
)
def pregeneration_readiness(request: Request, context: AuthContext = Depends(current_user)):
    """Whether a background picture could start now, and if not, why not."""

    return services(request).scene_backlog.production_readiness(context.user_id)


@router.post("/scene-backlog/proposals", response_model=SceneProposalResponse, tags=["media"])
def propose_scenes_for_persona(
    body: SceneProposalRequest,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    """Propose pictures from what the persona already is. Nothing is approved."""

    return services(request).scene_backlog.propose_from_persona(context.user_id, body.persona_id, limit=body.limit)


@router.put(
    "/scene-backlog/{entry_id}/state",
    response_model=SceneBacklogRepresentation,
    tags=["media"],
)
def update_scene_state(
    entry_id: str,
    body: SceneBacklogStateUpdate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).scene_backlog.set_state(context.user_id, entry_id, body.state)


@router.delete("/scene-backlog/{entry_id}", status_code=204, tags=["media"])
def delete_scene(entry_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).scene_backlog.remove(context.user_id, entry_id):
        raise NotFoundError("backlog entry not found")
    return Response(status_code=204)


@router.get("/media-library", response_model=LibraryEntryListResponse, tags=["media"])
def media_library_entries(
    request: Request,
    persona_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).media_library.entries(context.user_id, persona_id=persona_id, limit=limit)}


@router.post("/media-library", response_model=LibraryEntryRepresentation, status_code=201, tags=["media"])
def add_media_library_entry(
    body: LibraryEntryCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).media_library.add_existing(
        context.user_id, media_id=body.media_id, persona_id=body.persona_id, scene=body.scene
    )


@router.delete("/media-library/{entry_id}", status_code=204, tags=["media"])
def delete_media_library_entry(entry_id: str, request: Request, context: AuthContext = Depends(current_user)):
    if not services(request).media_library.remove(context.user_id, entry_id):
        raise NotFoundError("library entry not found")
    return Response(status_code=204)


@router.get(
    "/media/{media_id}/journal",
    response_model=MediaJournalRepresentation,
    tags=["media"],
)
def media_journal_for_media(media_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_journal.journal_for_media(context.user_id, media_id)


@router.get("/media-journals", response_model=MediaJournalListResponse, tags=["media"])
def media_journals(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).media_journal.journals(context.user_id, limit=limit, offset=offset)}


@router.get(
    "/media-journals/{journal_id}",
    response_model=MediaJournalRepresentation,
    tags=["media"],
)
def media_journal(journal_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).media_journal.journal(context.user_id, journal_id)


@router.get("/media-journals/{journal_id}/export", tags=["media"])
def media_journal_export(journal_id: str, request: Request, context: AuthContext = Depends(current_user)):
    filename, content = services(request).media_journal.export(context.user_id, journal_id)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    app_services = services(request)

    def record() -> None:
        # A database read and a log write: worker-thread work, like every
        # other synchronous handler gets automatically.
        user_id = None
        if token:
            try:
                user_id = app_services.resources.authenticate(token).user_id
            except Exception:
                user_id = None
        app_services.operations.client_event(user_id, payload if isinstance(payload, dict) else {})

    await asyncio.to_thread(record)
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
