"""Make persona image delivery direct and recover legacy chat media."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time

from alembic import op
import sqlalchemy as sa


revision = "0018_human_image_delivery"
down_revision = "0017_chat_attachments"
branch_labels = None
depends_on = None


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _kind_for_capability(key: str) -> str | None:
    if key == "media.generate_image":
        return "image"
    if key == "media.generate_video":
        return "video"
    return None


def _media_row(connection, *, media_id: str | None, user_id: str, chat_id: str, kind: str):
    if not media_id:
        return None
    row = connection.execute(
        sa.text(
            "SELECT id,local_path FROM media_files "
            "WHERE id=:media_id AND user_id=:user_id AND chat_id=:chat_id AND kind=:kind"
        ),
        {
            "media_id": media_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "kind": kind,
        },
    ).first()
    if not row:
        return None
    database_path = getattr(connection.engine.url, "database", None)
    if not database_path:
        return None
    root = (Path(database_path).resolve().parent / ("images" if kind == "image" else "videos")).resolve()
    try:
        candidate = Path(row[1]).resolve()
        candidate.relative_to(root)
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            return None
    except (OSError, ValueError):
        return None
    return row


def _identity_state(
    result: dict,
    attempt_status: str | None = None,
    plan_conditioning: dict | None = None,
) -> str:
    conditioning = result.get("identityConditioning")
    if isinstance(conditioning, dict):
        if conditioning.get("status") == "unconditioned":
            return "unconditioned"
        if conditioning.get("claim_status") == "verified" or conditioning.get("verification_status") == "passed":
            return "verified"
        if conditioning.get("status") == "conditioned":
            return "unverified"
    if (plan_conditioning or {}).get("status") == "unconditioned":
        return "unconditioned"
    identity_conditioned = (plan_conditioning or {}).get("required") is True or (plan_conditioning or {}).get(
        "status"
    ) == "ready"
    if identity_conditioned and attempt_status == "passed":
        return "verified"
    if identity_conditioned and attempt_status in {"failed", "unverified", "error"}:
        return "unverified"
    return "not_applicable"


def _candidate_for_request(
    connection,
    *,
    request_id: str,
    user_id: str,
    chat_id: str,
    kind: str,
    result_json: str | None,
):
    result = _json_object(result_json)
    result_media_id = result.get("mediaId")
    if isinstance(result_media_id, str):
        media = _media_row(
            connection,
            media_id=result_media_id,
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
        )
        if media:
            return media[0], _identity_state(result), result

    plan = connection.execute(
        sa.text(
            "SELECT id,identity_conditioning_json FROM media_execution_plans "
            "WHERE capability_request_id=:request_id AND user_id=:user_id"
        ),
        {"request_id": request_id, "user_id": user_id},
    ).first()
    if not plan:
        return None, "not_applicable", result
    conditioning = _json_object(plan[1])
    allow_incomplete_identity = (
        conditioning.get("status") != "ready" or conditioning.get("failure_policy") != "block_claim"
    )

    attempt = connection.execute(
        sa.text(
            "SELECT attempts.media_id,attempts.status "
            "FROM media_generation_attempts AS attempts "
            "JOIN media_files AS media ON media.id=attempts.media_id "
            "WHERE attempts.media_plan_id=:plan_id "
            "AND attempts.user_id=:user_id "
            "AND attempts.status IN ('passed','unverified','failed','error') "
            "AND (attempts.status NOT IN ('failed','error') OR :allow_incomplete_identity=1) "
            "AND (attempts.status!='error' OR attempts.error_code='interrupted') "
            "AND media.user_id=:user_id AND media.chat_id=:chat_id AND media.kind=:kind "
            "ORDER BY "
            "CASE attempts.status "
            "WHEN 'passed' THEN 0 WHEN 'unverified' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,"
            "COALESCE(attempts.score,-1) DESC,attempts.attempt_number DESC "
            "LIMIT 1"
        ),
        {
            "plan_id": plan[0],
            "user_id": user_id,
            "chat_id": chat_id,
            "kind": kind,
            "allow_incomplete_identity": int(allow_incomplete_identity),
        },
    ).first()
    if attempt:
        media = _media_row(
            connection,
            media_id=attempt[0],
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
        )
        if media:
            return attempt[0], _identity_state(result, attempt[1], conditioning), result

    if not allow_incomplete_identity:
        return None, "not_applicable", result
    media = connection.execute(
        sa.text(
            "SELECT id FROM media_files "
            "WHERE generation_plan_id=:plan_id AND user_id=:user_id "
            "AND chat_id=:chat_id AND kind=:kind "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        ),
        {
            "plan_id": plan[0],
            "user_id": user_id,
            "chat_id": chat_id,
            "kind": kind,
        },
    ).first()
    if media and _media_row(
        connection,
        media_id=media[0],
        user_id=user_id,
        chat_id=chat_id,
        kind=kind,
    ):
        return media[0], _identity_state(result, plan_conditioning=conditioning), result
    return None, "not_applicable", result


def _message_anchor(
    connection,
    *,
    chat_id: str,
    turn_id: str | None,
    media_id: str | None,
    created_at: int,
):
    if turn_id:
        message = connection.execute(
            sa.text(
                "SELECT turns.assistant_message_id,messages.text "
                "FROM conversation_turns AS turns "
                "JOIN messages ON messages.id=turns.assistant_message_id "
                "WHERE turns.id=:turn_id AND turns.chat_id=:chat_id"
            ),
            {"turn_id": turn_id, "chat_id": chat_id},
        ).first()
        if message:
            content_url = f"/api/v1/media/{media_id}" if media_id else None
            return message[0], bool(content_url and content_url in str(message[1] or ""))
    if media_id:
        content_url = f"/api/v1/media/{media_id}"
        message = connection.execute(
            sa.text(
                "SELECT id FROM messages "
                "WHERE chat_id=:chat_id AND role='assistant' AND instr(text,:content_url)>0 "
                "ORDER BY created_at,id LIMIT 1"
            ),
            {"chat_id": chat_id, "content_url": content_url},
        ).first()
        if message:
            return message[0], True
    stamp = int(created_at or 0)
    if not stamp:
        stamp = (
            int(
                connection.execute(
                    sa.text("SELECT MAX(created_at) FROM messages WHERE chat_id=:chat_id"),
                    {"chat_id": chat_id},
                ).scalar_one_or_none()
                or 0
            )
            + 1
        )
    message_id = secrets.token_hex(8)
    connection.execute(
        sa.text(
            "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(:id,:chat_id,'assistant','',:created_at)"
        ),
        {"id": message_id, "chat_id": chat_id, "created_at": stamp},
    )
    connection.execute(
        sa.text("UPDATE chats SET updated_at=MAX(updated_at,:updated_at) WHERE id=:chat_id"),
        {"updated_at": stamp, "chat_id": chat_id},
    )
    return message_id, False


def _add_event_once(
    connection,
    *,
    request_id: str,
    user_id: str,
    action: str,
    from_status: str | None,
    to_status: str | None,
    detail: dict,
    created_at: int,
) -> None:
    exists = connection.execute(
        sa.text(
            "SELECT 1 FROM capability_events "
            "WHERE capability_request_id=:request_id AND action=:action "
            "AND instr(detail_json,'migration_0018')>0 LIMIT 1"
        ),
        {"request_id": request_id, "action": action},
    ).first()
    if exists:
        return
    latest = connection.execute(
        sa.text("SELECT MAX(created_at) FROM capability_events WHERE capability_request_id=:request_id"),
        {"request_id": request_id},
    ).scalar_one_or_none()
    stamp = max(int(created_at or 0), int(latest or 0) + 1)
    connection.execute(
        sa.text(
            "INSERT INTO capability_events("
            "id,user_id,capability_request_id,action,from_status,to_status,detail_json,created_at"
            ") VALUES(:id,:user_id,:request_id,:action,:from_status,:to_status,:detail,:created_at)"
        ),
        {
            "id": secrets.token_hex(12),
            "user_id": user_id,
            "request_id": request_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "detail": json.dumps(detail, separators=(",", ":")),
            "created_at": stamp,
        },
    )


def _complete_request_with_media(
    connection,
    *,
    request_id: str,
    user_id: str,
    chat_id: str,
    kind: str,
    previous_status: str,
    result: dict,
    media_id: str,
    completed_at: int,
) -> None:
    content_url = f"/api/v1/media/{media_id}"
    result = dict(result)
    result.update(
        {
            "ok": True,
            "mediaId": media_id,
            "chatId": chat_id,
            "imageUrl" if kind == "image" else "videoUrl": content_url,
        }
    )
    result.setdefault(
        "text",
        (
            f"Here is your generated image.\n\n![Generated image]({content_url})"
            if kind == "image"
            else f"Here is your generated video.\n\n[Download generated video]({content_url})"
        ),
    )
    connection.execute(
        sa.text(
            "UPDATE capability_requests SET status='completed',result_json=:result_json,"
            "error_code=NULL,error_message=NULL,completed_at=COALESCE(completed_at,:completed_at) "
            "WHERE id=:request_id"
        ),
        {
            "result_json": json.dumps(result, separators=(",", ":"), ensure_ascii=False),
            "completed_at": completed_at,
            "request_id": request_id,
        },
    )
    connection.execute(
        sa.text(
            "UPDATE async_jobs SET status='completed',progress='Completed',error=NULL,"
            "result_json=:result_json,completed_at=COALESCE(completed_at,:completed_at),"
            "updated_at=MAX(updated_at,:completed_at) "
            "WHERE capability_request_id=:request_id"
        ),
        {
            "result_json": json.dumps(result, separators=(",", ":"), ensure_ascii=False),
            "completed_at": completed_at,
            "request_id": request_id,
        },
    )
    _add_event_once(
        connection,
        request_id=request_id,
        user_id=user_id,
        action="completed",
        from_status=previous_status,
        to_status="completed",
        detail={"source": "migration_0018", "recovered_media_id": media_id},
        created_at=completed_at,
    )


def _fail_missing_artifact(
    connection,
    *,
    request_id: str,
    user_id: str,
    kind: str,
    previous_status: str,
    completed_at: int,
    attachment_id: str | None = None,
) -> str:
    message = f"The generated {'picture' if kind == 'image' else 'video'} file is no longer available."
    connection.execute(
        sa.text(
            "UPDATE capability_requests SET status='failed',error_code='artifact_missing',"
            "error_message=:message,completed_at=COALESCE(completed_at,:completed_at) "
            "WHERE id=:request_id"
        ),
        {
            "message": message,
            "completed_at": completed_at,
            "request_id": request_id,
        },
    )
    connection.execute(
        sa.text(
            "UPDATE async_jobs SET status='failed',error=:message,"
            "completed_at=COALESCE(completed_at,:completed_at),"
            "updated_at=MAX(updated_at,:completed_at) "
            "WHERE capability_request_id=:request_id"
        ),
        {
            "message": message,
            "completed_at": completed_at,
            "request_id": request_id,
        },
    )
    if attachment_id:
        connection.execute(
            sa.text(
                "UPDATE chat_attachments SET status='failed',media_id=NULL,safe_error=:message,"
                "retry_available=1,updated_at=:completed_at,completed_at=:completed_at "
                "WHERE id=:attachment_id"
            ),
            {
                "message": message,
                "completed_at": completed_at,
                "attachment_id": attachment_id,
            },
        )
    _add_event_once(
        connection,
        request_id=request_id,
        user_id=user_id,
        action="failed",
        from_status=previous_status,
        to_status="failed",
        detail={"source": "migration_0018", "reason": "artifact_missing"},
        created_at=completed_at,
    )
    return message


def _ensure_attachment_for_request(connection, request, stamp: int) -> None:
    (
        request_id,
        user_id,
        chat_id,
        turn_id,
        capability_key,
        status,
        result_json,
        error_message,
        requested_at,
        completed_at,
    ) = request
    kind = _kind_for_capability(capability_key)
    if not kind or not chat_id:
        return
    existing_attachment = connection.execute(
        sa.text(
            "SELECT id,status,media_id,identity_state FROM chat_attachments WHERE capability_request_id=:request_id"
        ),
        {"request_id": request_id},
    ).first()

    media_id, identity_state, result = _candidate_for_request(
        connection,
        request_id=request_id,
        user_id=user_id,
        chat_id=chat_id,
        kind=kind,
        result_json=result_json,
    )
    if (
        not media_id
        and existing_attachment
        and existing_attachment[2]
        and _media_row(
            connection,
            media_id=existing_attachment[2],
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
        )
    ):
        media_id = existing_attachment[2]
        identity_state = existing_attachment[3]
    if status in {"pending_confirmation", "cancelled", "denied", "expired"}:
        media_id = None
        identity_state = "not_applicable"
    if existing_attachment:
        if (
            media_id
            and status not in {"pending_confirmation", "cancelled", "denied", "expired"}
            and (status != "completed" or existing_attachment[1] != "completed" or existing_attachment[2] != media_id)
        ):
            recovered_at = completed_at or stamp
            _complete_request_with_media(
                connection,
                request_id=request_id,
                user_id=user_id,
                chat_id=chat_id,
                kind=kind,
                previous_status=status,
                result=result,
                media_id=media_id,
                completed_at=recovered_at,
            )
            connection.execute(
                sa.text(
                    "UPDATE chat_attachments SET status='completed',media_id=:media_id,"
                    "identity_state=:identity_state,safe_error=NULL,retry_available=0,"
                    "updated_at=:completed_at,completed_at=:completed_at WHERE id=:attachment_id"
                ),
                {
                    "media_id": media_id,
                    "identity_state": identity_state,
                    "completed_at": recovered_at,
                    "attachment_id": existing_attachment[0],
                },
            )
        elif not media_id and (
            status == "completed"
            or (
                existing_attachment[1] == "completed"
                and status not in {"pending_confirmation", "cancelled", "denied", "expired"}
            )
        ):
            _fail_missing_artifact(
                connection,
                request_id=request_id,
                user_id=user_id,
                kind=kind,
                previous_status=status,
                completed_at=completed_at or stamp,
                attachment_id=existing_attachment[0],
            )
        return
    anchor, already_embedded = _message_anchor(
        connection,
        chat_id=chat_id,
        turn_id=turn_id,
        media_id=media_id,
        created_at=completed_at or requested_at or stamp,
    )
    if already_embedded and media_id:
        return

    attachment_status = {
        "pending_confirmation": "queued",
        "queued": "queued",
        "running": "running",
        "completed": "failed",
        "failed": "failed",
        "cancelled": "cancelled",
        "denied": "cancelled",
        "expired": "cancelled",
    }.get(status, "failed")
    retry_available = 1 if attachment_status in {"failed", "cancelled"} else 0
    attachment_completed_at = completed_at if attachment_status in {"failed", "cancelled"} else None
    safe_error = str(error_message or "")[:500] or None
    if media_id:
        attachment_status = "completed"
        retry_available = 0
        attachment_completed_at = completed_at or stamp
        _complete_request_with_media(
            connection,
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
            previous_status=status,
            result=result,
            media_id=media_id,
            completed_at=attachment_completed_at,
        )
    elif status == "completed":
        safe_error = _fail_missing_artifact(
            connection,
            request_id=request_id,
            user_id=user_id,
            kind=kind,
            previous_status=status,
            completed_at=attachment_completed_at or stamp,
        )
    connection.execute(
        sa.text(
            "INSERT INTO chat_attachments("
            "id,user_id,chat_id,assistant_message_id,capability_request_id,kind,status,media_id,"
            "identity_state,safe_error,retry_available,created_at,updated_at,completed_at"
            ") VALUES("
            ":id,:user_id,:chat_id,:assistant_message_id,:request_id,:kind,:status,:media_id,"
            ":identity_state,:safe_error,:retry_available,:created_at,:updated_at,:completed_at)"
        ),
        {
            "id": secrets.token_hex(12),
            "user_id": user_id,
            "chat_id": chat_id,
            "assistant_message_id": anchor,
            "request_id": request_id,
            "kind": kind,
            "status": attachment_status,
            "media_id": media_id,
            "identity_state": identity_state,
            "safe_error": safe_error,
            "retry_available": retry_available,
            "created_at": requested_at or stamp,
            "updated_at": completed_at or stamp,
            "completed_at": attachment_completed_at,
        },
    )


def _synthetic_request_for_media(
    connection,
    *,
    media_id: str,
    user_id: str,
    chat_id: str,
    kind: str,
    created_at: int,
    job_id: str | None = None,
    result: dict | None = None,
) -> str | None:
    content_url = f"/api/v1/media/{media_id}"
    embedded = connection.execute(
        sa.text("SELECT 1 FROM messages WHERE chat_id=:chat_id AND instr(text,:content_url)>0 LIMIT 1"),
        {"chat_id": chat_id, "content_url": content_url},
    ).first()
    if embedded:
        return None
    key = f"migration:legacy-{'job:' + job_id if job_id else 'media:' + media_id}"
    existing = connection.execute(
        sa.text("SELECT id FROM capability_requests WHERE user_id=:user_id AND idempotency_key=:key"),
        {"user_id": user_id, "key": key},
    ).first()
    request_id = existing[0] if existing else secrets.token_hex(12)
    result = dict(result or {})
    result.update(
        {
            "ok": True,
            "mediaId": media_id,
            "chatId": chat_id,
            "imageUrl" if kind == "image" else "videoUrl": content_url,
        }
    )
    result.setdefault(
        "text",
        (
            f"Here is your generated image.\n\n![Generated image]({content_url})"
            if kind == "image"
            else f"Here is your generated video.\n\n[Download generated video]({content_url})"
        ),
    )
    if not existing:
        connection.execute(
            sa.text(
                "INSERT INTO capability_requests("
                "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,"
                "permission_mode_effective,idempotency_key,result_json,requested_at,started_at,completed_at"
                ") VALUES("
                ":id,:user_id,:chat_id,:capability_key,'{}','completed','explicit','explicit',"
                ":key,:result_json,:created_at,:created_at,:created_at)"
            ),
            {
                "id": request_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "capability_key": f"media.generate_{kind}",
                "key": key,
                "result_json": json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                "created_at": created_at,
            },
        )
        _add_event_once(
            connection,
            request_id=request_id,
            user_id=user_id,
            action="requested",
            from_status=None,
            to_status="completed",
            detail={"source": "migration_0018", "legacy_media_id": media_id},
            created_at=created_at,
        )
    if job_id:
        connection.execute(
            sa.text(
                "UPDATE async_jobs SET capability_request_id=:request_id WHERE id=:job_id "
                "AND capability_request_id IS NULL"
            ),
            {"request_id": request_id, "job_id": job_id},
        )
    if connection.execute(
        sa.text("SELECT 1 FROM chat_attachments WHERE capability_request_id=:request_id"),
        {"request_id": request_id},
    ).first():
        return request_id
    anchor, _embedded = _message_anchor(
        connection,
        chat_id=chat_id,
        turn_id=None,
        media_id=None,
        created_at=created_at,
    )
    connection.execute(
        sa.text(
            "INSERT INTO chat_attachments("
            "id,user_id,chat_id,assistant_message_id,capability_request_id,kind,status,media_id,"
            "identity_state,retry_available,created_at,updated_at,completed_at"
            ") VALUES("
            ":id,:user_id,:chat_id,:assistant_message_id,:request_id,:kind,'completed',:media_id,"
            "'not_applicable',0,:created_at,:created_at,:created_at)"
        ),
        {
            "id": secrets.token_hex(12),
            "user_id": user_id,
            "chat_id": chat_id,
            "assistant_message_id": anchor,
            "request_id": request_id,
            "kind": kind,
            "media_id": media_id,
            "created_at": created_at,
        },
    )
    return request_id


def _backfill_legacy_attachments(connection, stamp: int) -> None:
    requests = connection.execute(
        sa.text(
            "SELECT id,user_id,chat_id,turn_id,capability_key,status,result_json,error_message,"
            "requested_at,completed_at "
            "FROM capability_requests "
            "WHERE chat_id IS NOT NULL "
            "AND capability_key IN ('media.generate_image','media.generate_video') "
            "ORDER BY requested_at,id"
        )
    ).fetchall()
    for request in requests:
        _ensure_attachment_for_request(connection, request, stamp)

    legacy_jobs = connection.execute(
        sa.text(
            "SELECT id,user_id,chat_id,kind,result_json,created_at,completed_at "
            "FROM async_jobs WHERE capability_request_id IS NULL AND chat_id IS NOT NULL "
            "AND kind IN ('image','video') AND status='completed' AND result_json IS NOT NULL "
            "ORDER BY created_at,id"
        )
    ).fetchall()
    for job_id, user_id, chat_id, kind, result_json, created_at, completed_at in legacy_jobs:
        result = _json_object(result_json)
        media_id = result.get("mediaId")
        if not isinstance(media_id, str) or not _media_row(
            connection,
            media_id=media_id,
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
        ):
            continue
        _synthetic_request_for_media(
            connection,
            media_id=media_id,
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
            created_at=completed_at or created_at or stamp,
            job_id=job_id,
            result=result,
        )


def _repair_generated_media_permissions(connection) -> None:
    database_path = getattr(connection.engine.url, "database", None)
    if not database_path:
        return
    data_dir = Path(database_path).resolve().parent
    roots = {
        "image": (data_dir / "images").resolve(),
        "video": (data_dir / "videos").resolve(),
    }
    rows = connection.execute(
        sa.text("SELECT kind,local_path FROM media_files WHERE kind IN ('image','video')")
    ).fetchall()
    for kind, local_path in rows:
        try:
            candidate = Path(local_path).resolve()
            candidate.relative_to(roots[kind])
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                continue
            os.chmod(candidate, 0o644)
        except (KeyError, OSError, TypeError, ValueError):
            continue


def _cancel_legacy_image_approvals(connection, stamp: int) -> None:
    requests = connection.execute(
        sa.text(
            "SELECT id,user_id,status FROM capability_requests "
            "WHERE capability_key='media.generate_image' AND status='pending_confirmation'"
        )
    ).fetchall()
    for request_id, user_id, previous_status in requests:
        connection.execute(
            sa.text(
                "UPDATE capability_requests SET status='cancelled',error_code=NULL,error_message=NULL,"
                "completed_at=:stamp WHERE id=:request_id"
            ),
            {"stamp": stamp, "request_id": request_id},
        )
        connection.execute(
            sa.text(
                "UPDATE chat_attachments SET status='cancelled',safe_error=NULL,retry_available=1,"
                "updated_at=:stamp,completed_at=:stamp WHERE capability_request_id=:request_id"
            ),
            {"stamp": stamp, "request_id": request_id},
        )
        _add_event_once(
            connection,
            request_id=request_id,
            user_id=user_id,
            action="cancelled",
            from_status=previous_status,
            to_status="cancelled",
            detail={"source": "migration_0018", "reason": "per_image_approval_removed"},
            created_at=stamp,
        )


def upgrade():
    op.execute(
        "ALTER TABLE personas ADD COLUMN allow_image_sends "
        "INTEGER NOT NULL DEFAULT 1 CHECK (allow_image_sends IN (0,1))"
    )
    connection = op.get_bind()
    for user_id, raw_preferences in connection.execute(sa.text("SELECT user_id,preferences_json FROM app_settings")):
        preferences = _json_object(raw_preferences)
        if "image_confirmation_policy" not in preferences:
            continue
        preferences.pop("image_confirmation_policy", None)
        connection.execute(
            sa.text("UPDATE app_settings SET preferences_json=:preferences WHERE user_id=:user_id"),
            {
                "user_id": user_id,
                "preferences": json.dumps(preferences, separators=(",", ":"), ensure_ascii=False),
            },
        )
    connection.execute(sa.text("DELETE FROM setting_values WHERE key='image_confirmation_policy'"))
    stamp = int(time.time())
    _backfill_legacy_attachments(connection, stamp)
    _cancel_legacy_image_approvals(connection, stamp)
    _repair_generated_media_permissions(connection)


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
