from pathlib import Path
import json
import os
import secrets
import sqlite3
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event

from app.secret_store import SECRET_STORE


ROOT = Path(__file__).resolve().parents[1]


def sqlite_url(path):
    return f"sqlite:///{Path(path).resolve().as_posix()}"


def build_engine(path):
    engine = create_engine(sqlite_url(path), future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def connect_sqlite(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_verified_backup(source, target):
    """Create a transactionally consistent SQLite backup and verify it before publishing."""
    source = Path(source)
    target = Path(target)
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    source_conn = sqlite3.connect(source, timeout=5)
    backup_conn = sqlite3.connect(temporary)
    try:
        source_conn.execute("PRAGMA busy_timeout=5000")
        source_conn.backup(backup_conn)
        result = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {result}")
        backup_conn.close()
        source_conn.close()
        os.replace(temporary, target)
        return True
    except Exception:
        backup_conn.close()
        source_conn.close()
        temporary.unlink(missing_ok=True)
        raise


def upgrade_database(path):
    engine = build_engine(path)
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", sqlite_url(path))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()


def _json_object(value):
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _contained_generated_media_path(data_dir: Path, kind: str, local_path: str | None) -> Path | None:
    if kind not in {"image", "video"} or not local_path:
        return None
    root = (data_dir / ("images" if kind == "image" else "videos")).resolve()
    try:
        candidate = Path(local_path).resolve()
        candidate.relative_to(root)
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            return None
    except (OSError, ValueError):
        return None
    return candidate


def _valid_generated_media(
    conn,
    data_dir: Path,
    *,
    media_id: str | None,
    user_id: str,
    chat_id: str,
    kind: str,
):
    if not media_id:
        return None
    row = conn.execute(
        "SELECT id,kind,local_path FROM media_files WHERE id=? AND user_id=? AND chat_id=? AND kind=?",
        (media_id, user_id, chat_id, kind),
    ).fetchone()
    if not row or not _contained_generated_media_path(data_dir, row["kind"], row["local_path"]):
        return None
    return row


def _inflight_media_candidate(conn, data_dir: Path, row):
    result = _json_object(row["result_json"])
    conditioning = _json_object(row["identity_conditioning_json"])
    allow_incomplete_identity = (
        conditioning.get("status") != "ready" or conditioning.get("failure_policy") != "block_claim"
    )
    result_media_id = result.get("mediaId")
    if isinstance(result_media_id, str):
        media = _valid_generated_media(
            conn,
            data_dir,
            media_id=result_media_id,
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            kind=row["kind"],
        )
        if media:
            return media, row["identity_state"], result, None

    if row["attachment_media_id"] and (allow_incomplete_identity or row["identity_state"] == "verified"):
        media = _valid_generated_media(
            conn,
            data_dir,
            media_id=row["attachment_media_id"],
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            kind=row["kind"],
        )
        if media:
            return media, row["identity_state"], result, None

    if not row["plan_id"]:
        return None, row["identity_state"], result, None
    attempt = conn.execute(
        "SELECT attempts.id,attempts.media_id,attempts.status "
        "FROM media_generation_attempts AS attempts "
        "JOIN media_files AS media ON media.id=attempts.media_id "
        "WHERE attempts.media_plan_id=? AND attempts.user_id=? "
        "AND (attempts.status IN ('passed','unverified') "
        "OR (?=1 AND attempts.status IN ('failed','running'))) "
        "AND media.user_id=? AND media.chat_id=? AND media.kind=? "
        "ORDER BY "
        "CASE attempts.status "
        "WHEN 'passed' THEN 0 WHEN 'unverified' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,"
        "COALESCE(attempts.score,-1) DESC,attempts.attempt_number DESC LIMIT 1",
        (
            row["plan_id"],
            row["user_id"],
            int(allow_incomplete_identity),
            row["user_id"],
            row["chat_id"],
            row["kind"],
        ),
    ).fetchone()
    if attempt:
        media = _valid_generated_media(
            conn,
            data_dir,
            media_id=attempt["media_id"],
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            kind=row["kind"],
        )
        if media:
            identity_conditioned = conditioning.get("required") is True or conditioning.get("status") == "ready"
            if conditioning.get("status") == "unconditioned":
                identity_state = "unconditioned"
            elif identity_conditioned and attempt["status"] == "passed":
                identity_state = "verified"
            elif identity_conditioned and attempt["status"] in {"unverified", "failed"}:
                identity_state = "unverified"
            else:
                identity_state = row["identity_state"]
            return media, identity_state, result, attempt["id"]

    attempt_states = {
        item[0]
        for item in conn.execute(
            "SELECT status FROM media_generation_attempts WHERE media_plan_id=?",
            (row["plan_id"],),
        ).fetchall()
    }
    recoverable_attempt_states = {"passed", "unverified"}
    if allow_incomplete_identity:
        recoverable_attempt_states.update({"running", "failed"})
    if attempt_states and not (attempt_states & recoverable_attempt_states):
        return None, row["identity_state"], result, None
    if not allow_incomplete_identity:
        return None, row["identity_state"], result, None
    media_rows = conn.execute(
        "SELECT id,kind,local_path FROM media_files "
        "WHERE generation_plan_id=? AND user_id=? AND chat_id=? AND kind=? "
        "ORDER BY created_at DESC,id DESC",
        (row["plan_id"], row["user_id"], row["chat_id"], row["kind"]),
    ).fetchall()
    for media in media_rows:
        if _contained_generated_media_path(data_dir, media["kind"], media["local_path"]):
            return media, row["identity_state"], result, None
    return None, row["identity_state"], result, None


def _reconcile_inflight_generated_media(conn, data_dir: Path, stamp: int) -> None:
    rows = conn.execute(
        "SELECT attachments.id AS attachment_id,"
        "attachments.media_id AS attachment_media_id,attachments.identity_state,"
        "requests.id AS request_id,requests.user_id,requests.chat_id,requests.status AS request_status,"
        "requests.result_json,attachments.kind,"
        "plans.id AS plan_id,plans.identity_conditioning_json "
        "FROM chat_attachments AS attachments "
        "JOIN capability_requests AS requests ON requests.id=attachments.capability_request_id "
        "LEFT JOIN media_execution_plans AS plans ON plans.capability_request_id=requests.id "
        "WHERE attachments.status IN ('queued','running') "
        "AND requests.status IN ('queued','running') "
        "AND attachments.kind IN ('image','video')"
    ).fetchall()
    for row in rows:
        media, identity_state, result, attempt_id = _inflight_media_candidate(conn, data_dir, row)
        if not media:
            continue
        media_id = media["id"]
        content_url = f"/api/v1/media/{media_id}"
        conditioning = _json_object(row["identity_conditioning_json"])
        if identity_state == "not_applicable":
            if conditioning.get("status") == "unconditioned":
                identity_state = "unconditioned"
            elif conditioning.get("status") == "ready":
                identity_state = "unverified"
        result.update(
            {
                "ok": True,
                "mediaId": media_id,
                "chatId": row["chat_id"],
                "imageUrl" if row["kind"] == "image" else "videoUrl": content_url,
            }
        )
        result.setdefault(
            "text",
            (
                f"Here is your generated image.\n\n![Generated image]({content_url})"
                if row["kind"] == "image"
                else f"Here is your generated video.\n\n[Download generated video]({content_url})"
            ),
        )
        serialized = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        conn.execute(
            "UPDATE chat_attachments SET status='completed',media_id=?,identity_state=?,"
            "safe_error=NULL,retry_available=0,updated_at=?,completed_at=? WHERE id=?",
            (media_id, identity_state, stamp, stamp, row["attachment_id"]),
        )
        conn.execute(
            "UPDATE capability_requests SET status='completed',result_json=?,error_code=NULL,"
            "error_message=NULL,completed_at=? WHERE id=?",
            (serialized, stamp, row["request_id"]),
        )
        conn.execute(
            "UPDATE async_jobs SET status='completed',progress='Completed',result_json=?,error=NULL,"
            "updated_at=?,completed_at=? WHERE capability_request_id=? AND status IN ('queued','running')",
            (serialized, stamp, stamp, row["request_id"]),
        )
        if attempt_id:
            conn.execute(
                "UPDATE media_generation_attempts SET status=?,media_id=?,completed_at=? "
                "WHERE id=? AND status='running'",
                (
                    "unverified" if identity_state in {"unverified", "unconditioned"} else "passed",
                    media_id,
                    stamp,
                    attempt_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE media_generation_attempts SET status=?,media_id=COALESCE(media_id,?),"
                "completed_at=? WHERE media_plan_id=? AND status='running'",
                (
                    "unverified" if identity_state in {"unverified", "unconditioned"} else "passed",
                    media_id,
                    stamp,
                    row["plan_id"],
                ),
            )
        existing = conn.execute(
            "SELECT 1 FROM capability_events WHERE capability_request_id=? "
            "AND action='completed' AND instr(detail_json,'startup_media_recovery')>0 LIMIT 1",
            (row["request_id"],),
        ).fetchone()
        if not existing:
            latest = conn.execute(
                "SELECT MAX(created_at) FROM capability_events WHERE capability_request_id=?",
                (row["request_id"],),
            ).fetchone()[0]
            event_stamp = max(stamp, int(latest or 0) + 1)
            conn.execute(
                "INSERT INTO capability_events("
                "id,user_id,capability_request_id,action,from_status,to_status,detail_json,created_at"
                ") VALUES(?,?,?,'completed',?,?,?,?)",
                (
                    secrets.token_hex(12),
                    row["user_id"],
                    row["request_id"],
                    row["request_status"],
                    "completed",
                    json.dumps(
                        {"source": "startup_media_recovery", "recovered_media_id": media_id},
                        separators=(",", ":"),
                    ),
                    event_stamp,
                ),
            )


def initialize_database(path, session_ttl_seconds, secret_store=None):
    secret_store = secret_store or SECRET_STORE
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    upgrade_database(path)
    conn = connect_sqlite(path)
    stamp = int(time.time())
    data_dir = Path(path).resolve().parent
    _reconcile_inflight_generated_media(conn, data_dir, stamp)
    conn.execute(
        "UPDATE sessions SET expires_at=created_at+? WHERE expires_at IS NULL",
        (session_ttl_seconds,),
    )
    conn.execute(
        "UPDATE async_jobs SET status='failed', error='interrupted by server restart', completed_at=?, updated_at=? WHERE status IN ('queued','running')",
        (stamp, stamp),
    )
    conn.execute(
        "UPDATE conversation_turns SET status='failed', error_code='interrupted', error_message='interrupted by server restart', completed_at=? WHERE status IN ('queued','running')",
        (stamp,),
    )
    conn.execute(
        "UPDATE capability_requests SET status='failed', error_code='interrupted', error_message='interrupted by server restart', completed_at=? WHERE status IN ('queued','running')",
        (stamp,),
    )
    conn.execute(
        "UPDATE chat_attachments SET status='failed', safe_error='Image generation was interrupted by a restart.', retry_available=1, completed_at=?, updated_at=? WHERE status IN ('queued','running')",
        (stamp, stamp),
    )
    conn.execute(
        "UPDATE task_model_runs SET status='failed', error_code='interrupted', error_message='interrupted by server restart', completed_at=? WHERE status='running'",
        (stamp,),
    )
    conn.execute(
        "UPDATE persona_identity_validations SET status='error', error_code='interrupted', error_message='interrupted by server restart', completed_at=? WHERE status IN ('queued','running')",
        (stamp,),
    )
    conn.execute(
        "UPDATE media_generation_attempts SET status='error', error_code='interrupted', error_message='interrupted by server restart', completed_at=? WHERE status='running'",
        (stamp,),
    )
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE COALESCE(is_admin,0)=1").fetchone()[0]
    if user_count and not admin_count:
        first = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
        if first:
            conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (first[0],))
    if secret_store.available:
        rows = conn.execute(
            "SELECT user_id,openai_api_key FROM app_settings WHERE openai_api_key IS NOT NULL AND openai_api_key!='' AND (openai_api_key_encrypted IS NULL OR openai_api_key_encrypted='')"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE app_settings SET openai_api_key=NULL, openai_api_key_encrypted=? WHERE user_id=?",
                (secret_store.encrypt(row[1]), row[0]),
            )
        encrypted_rows = conn.execute(
            "SELECT openai_api_key_encrypted FROM app_settings WHERE openai_api_key_encrypted IS NOT NULL AND openai_api_key_encrypted!=''"
        ).fetchall()
        for row in encrypted_rows:
            secret_store.decrypt(row[0])
        identity_secret_rows = conn.execute(
            "SELECT api_key_encrypted FROM identity_validation_settings WHERE api_key_encrypted IS NOT NULL AND api_key_encrypted!=''"
        ).fetchall()
        for row in identity_secret_rows:
            secret_store.decrypt(row[0])
    else:
        secret_count = conn.execute(
            "SELECT (SELECT COUNT(*) FROM app_settings WHERE (openai_api_key IS NOT NULL AND openai_api_key!='') OR (openai_api_key_encrypted IS NOT NULL AND openai_api_key_encrypted!='')) + (SELECT COUNT(*) FROM identity_validation_settings WHERE api_key_encrypted IS NOT NULL AND api_key_encrypted!='')"
        ).fetchone()[0]
        if secret_count:
            conn.close()
            raise RuntimeError("NICE_ASSISTANT_MASTER_KEY is required for the provider secrets in this database")
    conn.commit()
    conn.close()
