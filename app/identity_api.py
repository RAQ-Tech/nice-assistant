from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.application import ApplicationServices
from app.resource_service import AuthContext
from app.runtime import SESSION_COOKIE


router = APIRouter(prefix="/api/v1")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentitySettingsWrite(StrictModel):
    provider: str = Field(pattern="^(disabled|compreface)$")
    base_url: str = Field(default="", max_length=2000)
    api_key: str | None = Field(default=None, max_length=2000)
    timeout_seconds: float = Field(default=15, ge=1, le=120)


class VisualIdentityWrite(StrictModel):
    appearance_description: str = Field(default="", max_length=8000)
    acceptance_threshold: float = Field(default=0.78, ge=0, le=1)
    max_generation_attempts: int = Field(default=2, ge=1, le=10)
    failure_policy: str = Field(default="block_claim", pattern="^(block_claim|show_unverified)$")


class IdentityConsentGrant(StrictModel):
    attested: bool


class IdentityReferenceFromMedia(StrictModel):
    media_id: str
    attested: bool


class IdentityReferenceRejection(StrictModel):
    reason: str = Field(default="", max_length=500)


class IdentityValidationCreate(StrictModel):
    media_id: str


def services(request: Request) -> ApplicationServices:
    return request.app.state.services


def current_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthContext:
    return services(request).resources.authenticate(session_token)


@router.get("/identity-validation/settings", tags=["visual identity"])
def get_identity_settings(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).identity.settings(context.user_id)


@router.put("/identity-validation/settings", tags=["visual identity"])
def update_identity_settings(
    body: IdentitySettingsWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.save_settings(context.user_id, body.model_dump())


@router.post("/identity-validation/check", tags=["visual identity"])
def check_identity_provider(request: Request, context: AuthContext = Depends(current_user)):
    return services(request).identity.check_provider(context.user_id)


@router.get("/personas/{persona_id}/visual-identity", tags=["visual identity"])
def get_visual_identity(persona_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).identity.get_profile(context.user_id, persona_id)


@router.put("/personas/{persona_id}/visual-identity", tags=["visual identity"])
def update_visual_identity(
    persona_id: str,
    body: VisualIdentityWrite,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.save_profile(context.user_id, persona_id, body.model_dump())


@router.post("/personas/{persona_id}/visual-identity/consent", tags=["visual identity"])
def grant_visual_identity_consent(
    persona_id: str,
    body: IdentityConsentGrant,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.grant_consent(context.user_id, persona_id, body.attested)


@router.delete("/personas/{persona_id}/visual-identity/consent", tags=["visual identity"])
def withdraw_visual_identity_consent(
    persona_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.withdraw_consent(context.user_id, persona_id)


@router.post("/personas/{persona_id}/visual-identity/references", tags=["visual identity"])
async def upload_visual_identity_reference(
    persona_id: str,
    request: Request,
    file: UploadFile = File(...),
    provenance: str = Form(default="user_upload"),
    attested: bool = Form(...),
    context: AuthContext = Depends(current_user),
):
    content = await file.read()
    return services(request).identity.add_reference(
        context.user_id,
        persona_id,
        content=content,
        provenance=provenance,
        attested=attested,
    )


@router.post("/personas/{persona_id}/visual-identity/references/from-media", tags=["visual identity"])
def add_visual_identity_reference_from_media(
    persona_id: str,
    body: IdentityReferenceFromMedia,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.add_reference_from_media(
        context.user_id,
        persona_id,
        body.media_id,
        attested=body.attested,
    )


@router.post("/identity-references/{reference_id}/approval", tags=["visual identity"])
def approve_visual_identity_reference(
    reference_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.review_reference(context.user_id, reference_id, approve=True)


@router.post("/identity-references/{reference_id}/rejection", tags=["visual identity"])
def reject_visual_identity_reference(
    reference_id: str,
    body: IdentityReferenceRejection,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.review_reference(
        context.user_id,
        reference_id,
        approve=False,
        reason=body.reason,
    )


@router.delete("/identity-references/{reference_id}", tags=["visual identity"])
def delete_visual_identity_reference(
    reference_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    services(request).identity.delete_reference(context.user_id, reference_id)
    return {"ok": True}


@router.get("/identity-references/{reference_id}/content", tags=["visual identity"])
def visual_identity_reference_content(
    reference_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    path = services(request).identity.reference_path(context.user_id, reference_id)
    return FileResponse(path, media_type="image/jpeg")


@router.post(
    "/personas/{persona_id}/visual-identity/validations",
    tags=["visual identity"],
    status_code=202,
)
def validate_persona_media(
    persona_id: str,
    body: IdentityValidationCreate,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return services(request).identity.validate_media(context.user_id, persona_id, body.media_id)


@router.get("/personas/{persona_id}/visual-identity/validations", tags=["visual identity"])
def list_visual_identity_validations(
    persona_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).identity.validations(context.user_id, persona_id, limit)}


@router.get("/personas/{persona_id}/visual-identity/history", tags=["visual identity"])
def visual_identity_history(
    persona_id: str,
    request: Request,
    context: AuthContext = Depends(current_user),
):
    return {"items": services(request).identity.history(context.user_id, persona_id)}


@router.get("/media/{media_id}/identity-status", tags=["visual identity"])
def media_identity_status(media_id: str, request: Request, context: AuthContext = Depends(current_user)):
    return services(request).identity.media_status(context.user_id, media_id)
