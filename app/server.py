import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
import signal
import threading
import re
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.job_queue import JobQueue, new_job
from app.memory_guard import MemoryBackpressureError, MemoryGuard
from model_residency import ResidencyPolicy, build_default_residency_manager

PORT = int(os.getenv("PORT", "3000"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.18.200:11434")
AUTOMATIC1111_BASE_URL = os.getenv("AUTOMATIC1111_BASE_URL", "http://127.0.0.1:7860")
COMFYUI_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/archives"))
AUDIO_HOT_LIMIT = int(os.getenv("AUDIO_HOT_LIMIT", "200"))
SESSION_COOKIE = "nice_assistant_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
ALLOW_PUBLIC_SIGNUP = os.getenv("ALLOW_PUBLIC_SIGNUP", "0").strip().lower() in {"1", "true", "yes", "on"}

LLM_ESTIMATED_VRAM_MB = int(os.getenv("LLM_ESTIMATED_VRAM_MB", "8192"))
IMAGE_ESTIMATED_VRAM_MB = int(os.getenv("IMAGE_ESTIMATED_VRAM_MB", "6144"))
VIDEO_ESTIMATED_VRAM_MB = int(os.getenv("VIDEO_ESTIMATED_VRAM_MB", "10240"))
MODEL_VRAM_BUDGET_MB = int(os.getenv("MODEL_VRAM_BUDGET_MB", "0"))
GPU_IDLE_HOLD_SECONDS_LLM = float(os.getenv("GPU_IDLE_HOLD_SECONDS_LLM", "0"))
GPU_IDLE_HOLD_SECONDS_IMAGE = float(os.getenv("GPU_IDLE_HOLD_SECONDS_IMAGE", "0"))
GPU_MIN_RESIDENCY_SECONDS = float(os.getenv("GPU_MIN_RESIDENCY_SECONDS", "0"))
MAX_MODEL_SWAPS_PER_MINUTE = int(os.getenv("MAX_MODEL_SWAPS_PER_MINUTE", "60"))
QUEUE_AFFINITY_WINDOW_MS = int(os.getenv("QUEUE_AFFINITY_WINDOW_MS", "0"))

AUDIO_DIR = DATA_DIR / "audio"
IMAGE_DIR = DATA_DIR / "images"
VIDEO_DIR = DATA_DIR / "videos"
LOG_DIR = DATA_DIR / "logs"
STT_RECORDINGS_DIR = DATA_DIR / "stt_recordings"
DB_PATH = DATA_DIR / "nice_assistant.db"
SETTINGS_JSON = DATA_DIR / "settings.json"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
)
logger = logging.getLogger("nice-assistant")
CLIENT_EVENT_LOG = "client-events"

MEMORY_GUARD = MemoryGuard(logger=logger)
MODEL_RESIDENCY = build_default_residency_manager(
    MODEL_VRAM_BUDGET_MB,
    policy=ResidencyPolicy(
        gpu_idle_hold_seconds_llm=GPU_IDLE_HOLD_SECONDS_LLM,
        gpu_idle_hold_seconds_image=GPU_IDLE_HOLD_SECONDS_IMAGE,
        gpu_min_residency_seconds=GPU_MIN_RESIDENCY_SECONDS,
        max_model_swaps_per_minute=MAX_MODEL_SWAPS_PER_MINUTE,
        queue_affinity_window_ms=QUEUE_AFFINITY_WINDOW_MS,
    ),
    memory_guard=MEMORY_GUARD,
)
JOB_QUEUE = JobQueue()


def log_generation_request(kind, provider, endpoint, payload=None):
    safe_payload = payload if isinstance(payload, dict) else {"value": str(payload or "")}
    try:
        serialized = json.dumps(safe_payload, sort_keys=True, default=str)
    except Exception:
        serialized = str(safe_payload)
    logger.info("generation request kind=%s provider=%s endpoint=%s payload=%s", kind, provider, endpoint, serialized)


IMAGE_QUALITY_ALIASES = {
    "standard": "medium",
    "hd": "high",
}
IMAGE_QUALITY_VALUES = {"low", "medium", "high", "auto", "none"}
SUPPORTED_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
SUPPORTED_VIDEO_MODELS = {"sora-2", "sora-2-pro"}
SUPPORTED_VIDEO_SECONDS = {"4", "8", "12"}
SUPPORTED_VIDEO_SIZES = {"720x1280", "1280x720", "1024x1792", "1792x1024"}
SUPPORTED_VIDEO_SIZES_BY_MODEL = {
    "sora-2": {"720x1280", "1280x720"},
    "sora-2-pro": SUPPORTED_VIDEO_SIZES,
}
MODEL_IMAGE_TAG_PATTERN = re.compile(r"<generate_image>(.*?)</generate_image>", re.IGNORECASE | re.DOTALL)
OPENAI_IMAGE_TERM_REPLACEMENTS = {
    "nsfw": "safe-for-work",
    "nude": "fully clothed",
    "naked": "fully clothed",
    "explicit sex": "romantic scene",
    "sexual": "romantic",
    "porn": "editorial",
    "fetish": "fashion concept",
    "gore": "dramatic",
    "graphic violence": "intense action",
}
CHAT_TITLE_PLACEHOLDERS = {"new chat", "untitled chat"}

CHAT_TITLE_MAX_LENGTH = 48
CHAT_TITLE_MIN_LENGTH = 8
CHAT_TITLE_FALLBACK_LENGTH = 40
CHAT_TITLE_OPENER_PATTERN = re.compile(
    r"^(?:hey|hi|hello|yo|good\s+(?:morning|afternoon|evening)|"
    r"can\s+you|could\s+you|would\s+you|please|i\s+need\s+help\s+with|"
    r"i\s+need\s+to|help\s+me\s+with)\b[\s,:-]*",
    re.IGNORECASE,
)


def ensure_dirs():
    for p in [DATA_DIR, AUDIO_DIR, IMAGE_DIR, VIDEO_DIR, LOG_DIR, STT_RECORDINGS_DIR, ARCHIVE_DIR, ARCHIVE_DIR / "audio", ARCHIVE_DIR / "logs", ARCHIVE_DIR / "db_backups"]:
        p.mkdir(parents=True, exist_ok=True)


def setup_file_logger():
    log_path = LOG_DIR / "events.log"
    if any(getattr(h, "baseFilename", None) == str(log_path) for h in logger.handlers):
        return
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=8, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    logger.addHandler(handler)


def generate_chat_title(text):
    source = "" if text is None else str(text)
    normalized = re.sub(r"\s+", " ", source).strip()
    if not normalized:
        return source[:CHAT_TITLE_FALLBACK_LENGTH]

    candidate = normalized
    while True:
        trimmed = CHAT_TITLE_OPENER_PATTERN.sub("", candidate, count=1).strip()
        if trimmed == candidate:
            break
        candidate = trimmed

    clause = re.split(r"[.!?;:\n]", candidate, maxsplit=1)[0].strip()
    if clause:
        candidate = clause

    candidate = candidate[:CHAT_TITLE_MAX_LENGTH].strip().rstrip(".,!?;:-")
    if len(candidate) < CHAT_TITLE_MIN_LENGTH:
        return normalized[:CHAT_TITLE_FALLBACK_LENGTH]
    return candidate


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS personas (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            avatar_url TEXT,
            system_prompt TEXT,
            personality_details TEXT,
            traits_json TEXT DEFAULT '{}',
            default_model TEXT,
            preferred_voice TEXT,
            preferred_tts_model TEXT,
            preferred_tts_speed TEXT,
            preferred_voice_openai TEXT,
            preferred_tts_model_openai TEXT,
            preferred_tts_speed_openai TEXT,
            preferred_voice_local TEXT,
            preferred_tts_model_local TEXT,
            preferred_tts_speed_local TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS persona_workspace_links (
            persona_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            PRIMARY KEY (persona_id, workspace_id)
        );
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT,
            persona_id TEXT,
            model_override TEXT,
            memory_mode TEXT DEFAULT 'auto',
            title TEXT,
            hidden_in_ui INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tier TEXT NOT NULL,
            tier_ref_id TEXT,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            user_id TEXT PRIMARY KEY,
            global_default_model TEXT,
            default_memory_mode TEXT DEFAULT 'auto',
            stt_provider TEXT DEFAULT 'disabled',
            tts_provider TEXT DEFAULT 'disabled',
            tts_format TEXT DEFAULT 'wav',
            openai_api_key TEXT,
            onboarding_done INTEGER DEFAULT 0,
            preferences_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS audio_files (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            persona_id TEXT,
            chat_id TEXT,
            format TEXT NOT NULL,
            local_path TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS media_files (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            chat_id TEXT,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            local_path TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_media_files_kind_filename ON media_files(kind, filename);
        CREATE TABLE IF NOT EXISTS async_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            chat_id TEXT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            cancel_requested INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            updated_at INTEGER NOT NULL,
            completed_at INTEGER,
            progress TEXT,
            result_json TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_async_jobs_user_status ON async_jobs(user_id, status, created_at);
        """
    )
    conn.commit()
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(async_jobs)").fetchall()}
    async_job_migrations = {
        "cancel_requested": "ALTER TABLE async_jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0",
        "started_at": "ALTER TABLE async_jobs ADD COLUMN started_at INTEGER",
        "updated_at": "ALTER TABLE async_jobs ADD COLUMN updated_at INTEGER",
        "completed_at": "ALTER TABLE async_jobs ADD COLUMN completed_at INTEGER",
        "progress": "ALTER TABLE async_jobs ADD COLUMN progress TEXT",
        "result_json": "ALTER TABLE async_jobs ADD COLUMN result_json TEXT",
        "error": "ALTER TABLE async_jobs ADD COLUMN error TEXT",
    }
    for col, statement in async_job_migrations.items():
        if col not in job_cols:
            conn.execute(statement)
            conn.commit()
    interrupted_at = now_ts()
    conn.execute(
        """
        UPDATE async_jobs
        SET status='failed',
            error='interrupted by server restart',
            completed_at=?,
            updated_at=?
        WHERE status IN ('queued', 'running')
        """,
        (interrupted_at, interrupted_at),
    )
    conn.commit()
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "is_admin" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        conn.commit()
    user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    admin_count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE COALESCE(is_admin,0)=1").fetchone()["c"]
    if user_count and not admin_count:
        first_user = conn.execute("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
        if first_user:
            conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (first_user["id"],))
            conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "expires_at" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN expires_at INTEGER")
        conn.execute("UPDATE sessions SET expires_at = created_at + ? WHERE expires_at IS NULL", (SESSION_TTL_SECONDS,))
        conn.commit()
    chat_cols = {r[1] for r in conn.execute("PRAGMA table_info(chats)").fetchall()}
    if "hidden_in_ui" not in chat_cols:
        conn.execute("ALTER TABLE chats ADD COLUMN hidden_in_ui INTEGER DEFAULT 0")
        conn.execute("UPDATE chats SET hidden_in_ui = 0 WHERE hidden_in_ui IS NULL")
        conn.commit()
    settings_cols = {r[1] for r in conn.execute("PRAGMA table_info(app_settings)").fetchall()}
    if "preferences_json" not in settings_cols:
        conn.execute("ALTER TABLE app_settings ADD COLUMN preferences_json TEXT DEFAULT '{}'")
        conn.execute("UPDATE app_settings SET preferences_json='{}' WHERE preferences_json IS NULL")
        conn.commit()
    persona_cols = {r[1] for r in conn.execute("PRAGMA table_info(personas)").fetchall()}
    if "personality_details" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN personality_details TEXT")
        conn.commit()
    if "traits_json" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN traits_json TEXT DEFAULT '{}'")
        conn.execute("UPDATE personas SET traits_json='{}' WHERE traits_json IS NULL")
        conn.commit()
    if "preferred_tts_model" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_model TEXT")
        conn.commit()
    if "preferred_tts_speed" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_speed TEXT")
        conn.commit()
    if "preferred_voice_openai" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_voice_openai TEXT")
        conn.commit()
    if "preferred_tts_model_openai" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_model_openai TEXT")
        conn.commit()
    if "preferred_tts_speed_openai" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_speed_openai TEXT")
        conn.commit()
    if "preferred_voice_local" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_voice_local TEXT")
        conn.commit()
    if "preferred_tts_model_local" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_model_local TEXT")
        conn.commit()
    if "preferred_tts_speed_local" not in persona_cols:
        conn.execute("ALTER TABLE personas ADD COLUMN preferred_tts_speed_local TEXT")
        conn.commit()
    conn.execute("CREATE TABLE IF NOT EXISTS persona_workspace_links (persona_id TEXT NOT NULL, workspace_id TEXT NOT NULL, PRIMARY KEY (persona_id, workspace_id))")
    conn.execute(
        "INSERT OR IGNORE INTO persona_workspace_links(persona_id, workspace_id) SELECT id, workspace_id FROM personas WHERE workspace_id IS NOT NULL"
    )
    conn.commit()
    conn.close()


class GracefulThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"{salt}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
        return base64.b64encode(dk).decode() == digest
    except Exception:
        return False


def now_ts():
    return int(time.time())


def current_user_row(uid):
    if not uid:
        return None
    conn = db_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return row


def is_admin(uid):
    row = current_user_row(uid)
    return bool(row and row["is_admin"])


def require_admin(uid):
    return is_admin(uid)


def mask_secret(value):
    raw = str(value or "")
    if not raw:
        return ""
    return f"********{raw[-4:]}"


def is_masked_secret(value):
    raw = str(value or "")
    return raw.startswith("********")


def redact_sensitive_text(text):
    redacted = str(text or "")
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}", r"\1[REDACTED]", redacted)
    labeled_secret_patterns = [
        r"(?i)((?:\"?openai_api_key\"?|\"?image_local_api_auth\"?|\"?api_auth\"?|\"?authorization\"?)\s*[=:]\s*\"?)([^\"\s,;}]+)",
    ]
    for pattern in labeled_secret_patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted


def settings_for_response(row):
    if not row:
        return {
            "default_memory_mode": "auto",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": "",
            "preferences_json": "{}",
        }
    data = dict(row)
    data["openai_api_key"] = mask_secret(data.get("openai_api_key"))
    return data


def record_media_file(uid, chat_id, kind, filename, local_path):
    conn = db_conn()
    conn.execute(
        "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) VALUES(?,?,?,?,?,?,?)",
        (secrets.token_hex(8), uid, chat_id, kind, filename, str(local_path), now_ts()),
    )
    conn.commit()
    conn.close()


def media_file_allowed(uid, kind, filename):
    if not uid or not filename:
        return False
    safe_filename = os.path.basename(filename)
    conn = db_conn()
    row = conn.execute(
        "SELECT user_id FROM media_files WHERE kind=? AND filename=? ORDER BY created_at DESC LIMIT 1",
        (kind, safe_filename),
    ).fetchone()
    conn.close()
    if row:
        return row["user_id"] == uid
    return safe_filename.startswith(f"{uid}_")


def truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def create_async_job(uid, chat_id, kind, progress="Queued"):
    job_id = secrets.token_hex(12)
    ts = now_ts()
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO async_jobs(
            id,user_id,chat_id,kind,status,cancel_requested,created_at,updated_at,progress
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (job_id, uid, chat_id, kind, "queued", 0, ts, ts, progress),
    )
    conn.commit()
    conn.close()
    return job_id


def get_async_job(uid, job_id):
    if not uid or not job_id:
        return None
    conn = db_conn()
    row = conn.execute("SELECT * FROM async_jobs WHERE id=? AND user_id=?", (job_id, uid)).fetchone()
    conn.close()
    return row


def update_async_job(job_id, **fields):
    allowed = {
        "status",
        "cancel_requested",
        "started_at",
        "updated_at",
        "completed_at",
        "progress",
        "result_json",
        "error",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = updates.get("updated_at", now_ts())
    assignments = ", ".join([f"{key}=?" for key in updates])
    conn = db_conn()
    conn.execute(f"UPDATE async_jobs SET {assignments} WHERE id=?", [*updates.values(), job_id])
    conn.commit()
    conn.close()


def async_job_cancel_requested(job_id):
    conn = db_conn()
    row = conn.execute("SELECT status, cancel_requested FROM async_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return True
    return bool(row["cancel_requested"]) or row["status"] == "cancelled"


def queue_position_for_async_job(job_id):
    return JOB_QUEUE.queue_position_for_metadata("async_job_id", job_id)


def async_job_response(row):
    result = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, ValueError):
            result = None
    queue_position = queue_position_for_async_job(row["id"]) if row["status"] == "queued" else None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "chatId": row["chat_id"],
        "progress": row["progress"] or "",
        "queuePosition": queue_position,
        "result": result,
        "error": row["error"] or "",
        "cancelRequested": bool(row["cancel_requested"]),
    }


def cancel_async_job(uid, job_id):
    row = get_async_job(uid, job_id)
    if not row:
        return None
    if row["status"] in {"completed", "failed", "cancelled"}:
        return row
    ts = now_ts()
    update_async_job(
        job_id,
        status="cancelled",
        cancel_requested=1,
        completed_at=ts,
        progress="Cancelled",
    )
    return get_async_job(uid, job_id)


def safe_async_job_error(exc):
    message = str(exc).strip() or exc.__class__.__name__
    return redact_sensitive_text(message)[:1000]


def submit_async_generation_job(
    *,
    async_job_id,
    uid,
    chat_id,
    kind,
    job_type,
    estimated_vram_mb,
    latency_class,
    execute,
    model_key=None,
    metadata=None,
):
    def _run_async_job():
        if async_job_cancel_requested(async_job_id):
            update_async_job(
                async_job_id,
                status="cancelled",
                cancel_requested=1,
                completed_at=now_ts(),
                progress="Cancelled",
            )
            return None
        update_async_job(
            async_job_id,
            status="running",
            started_at=now_ts(),
            progress="Running",
        )
        try:
            result = execute(lambda: async_job_cancel_requested(async_job_id))
            if async_job_cancel_requested(async_job_id):
                update_async_job(
                    async_job_id,
                    status="cancelled",
                    cancel_requested=1,
                    completed_at=now_ts(),
                    progress="Cancelled",
                )
                return None
            update_async_job(
                async_job_id,
                status="completed",
                completed_at=now_ts(),
                progress="Completed",
                result_json=json.dumps(result or {}, default=str),
                error=None,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - expose a safe polling error
            logger.exception("async job failed id=%s kind=%s user_id=%s", async_job_id, kind, uid)
            update_async_job(
                async_job_id,
                status="failed",
                completed_at=now_ts(),
                progress="Failed",
                error=safe_async_job_error(exc),
            )
            return None

    queue_job = new_job(
        job_type=job_type,
        user_id=uid,
        chat_id=chat_id,
        estimated_vram_mb=estimated_vram_mb,
        latency_class=latency_class,
        model_key=model_key,
        metadata={**(metadata or {}), "async_job_id": async_job_id, "async_kind": kind},
        execute=_run_async_job,
    )
    JOB_QUEUE.submit(queue_job)
    return queue_job


def generate_chat_title_from_first_user_message(text: str, max_len: int = 40) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return ""
    return normalized[:max_len]


def chat_title_needs_autogeneration(title: str | None) -> bool:
    normalized = (title or "").strip()
    if not normalized:
        return True
    return normalized.lower() in CHAT_TITLE_PLACEHOLDERS


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def rotate_audio_cache():
    files = sorted([p for p in AUDIO_DIR.glob("*") if p.is_file()], key=lambda p: p.stat().st_mtime)
    while len(files) > AUDIO_HOT_LIMIT:
        oldest = files.pop(0)
        shutil.move(str(oldest), ARCHIVE_DIR / "audio" / oldest.name)


def backup_db_if_needed():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = ARCHIVE_DIR / "db_backups" / f"nice_assistant_{stamp}.db"
    if not target.exists() and DB_PATH.exists():
        shutil.copy2(DB_PATH, target)


def rotate_logs(limit=50):
    logs = sorted([p for p in LOG_DIR.glob("*.log") if p.is_file()], key=lambda p: p.stat().st_mtime)
    while len(logs) > limit:
        f = logs.pop(0)
        shutil.move(str(f), ARCHIVE_DIR / "logs" / f.name)


def ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=6) as r:
            data = json.loads(r.read().decode())
            return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def call_ollama(model, messages, options=None):
    started = time.monotonic()
    body = {"model": model, "messages": messages, "stream": False}
    if options:
        body["options"] = options
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
            return data.get("message", {}).get("content", "")
    finally:
        elapsed = time.monotonic() - started
        logger.info("ollama request complete model=%s duration_ms=%d message_count=%d", model, int(elapsed * 1000), len(messages))


def execute_chat_model_job(model, messages, model_options, prefs=None):
    MODEL_RESIDENCY.update_policy(**parse_residency_policy_preferences(prefs or {}))
    MODEL_RESIDENCY.ensure_loaded("llm", LLM_ESTIMATED_VRAM_MB, model_id=model)
    return call_ollama(model, messages, model_options)


def parse_model_options(payload_settings):
    if not isinstance(payload_settings, dict):
        return {}
    schema = {
        "temperature": float,
        "top_p": float,
        "num_predict": int,
        "presence_penalty": float,
        "frequency_penalty": float,
    }
    options = {}
    for key, caster in schema.items():
        val = payload_settings.get(key)
        if val is None or val == "":
            continue
        try:
            options[key] = caster(val)
        except (TypeError, ValueError):
            continue
    return options


def parse_traits(raw_traits):
    if not raw_traits:
        return {
            "warmth": 50,
            "creativity": 50,
            "directness": 50,
            "conversational": 50,
            "casual": 50,
            "gender": "unspecified",
            "gender_other": "",
            "age": "",
        }
    try:
        parsed = json.loads(raw_traits) if isinstance(raw_traits, str) else dict(raw_traits)
    except (TypeError, ValueError):
        parsed = {}
    return {
        "warmth": int(parsed.get("warmth", 50)),
        "creativity": int(parsed.get("creativity", 50)),
        "directness": int(parsed.get("directness", 50)),
        "conversational": int(parsed.get("conversational", 50)),
        "casual": int(parsed.get("casual", 50)),
        "gender": str(parsed.get("gender", "unspecified") or "unspecified"),
        "gender_other": str(parsed.get("gender_other", "") or ""),
        "age": str(parsed.get("age", "") or ""),
    }


def persona_instruction_block(persona_row):
    if not persona_row:
        return ""
    traits = parse_traits(persona_row["traits_json"])
    warmth = "high" if traits["warmth"] >= 67 else ("low" if traits["warmth"] <= 33 else "moderate")
    creativity = "high" if traits["creativity"] >= 67 else ("low" if traits["creativity"] <= 33 else "moderate")
    directness = "high" if traits["directness"] >= 67 else ("low" if traits["directness"] <= 33 else "moderate")
    conversational = "conversational" if traits["conversational"] >= 60 else ("informational" if traits["conversational"] <= 40 else "balanced")
    casual = "casual" if traits["casual"] >= 60 else ("professional" if traits["casual"] <= 40 else "balanced")

    lines = [
        f"You are the persona named '{persona_row['name']}'. If asked your name or identity in this chat, answer as this persona.",
        f"Tone controls: warmth={warmth} ({traits['warmth']}/100), creativity={creativity} ({traits['creativity']}/100), directness={directness} ({traits['directness']}/100).",
        f"Style controls: conversational_vs_informational={conversational} ({traits['conversational']}/100), casual_vs_professional={casual} ({traits['casual']}/100).",
    ]
    if traits["gender"] == "other" and traits["gender_other"]:
        lines.append(f"Persona gender: {traits['gender_other']}")
    elif traits["gender"] != "unspecified":
        lines.append(f"Persona gender: {traits['gender']}")
    if traits["age"]:
        lines.append(f"Persona age: {traits['age']}")
    if persona_row["personality_details"]:
        lines.append(f"Persona details: {persona_row['personality_details']}")
    if persona_row["system_prompt"]:
        lines.append(persona_row["system_prompt"])
    return "\n".join(lines)


def parse_multipart_form_data(content_type, body):
    if not content_type or "multipart/form-data" not in content_type:
        return {}
    mime_bytes = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode() + body
    message = BytesParser(policy=default).parsebytes(mime_bytes)
    fields = {}
    if not message.is_multipart():
        return fields
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[name] = {
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "value": part.get_payload(decode=True) or b"",
        }
    return fields

def normalize_tts_speed(speed):
    try:
        parsed = float(speed)
    except (TypeError, ValueError):
        return 1.0
    return min(4.0, max(0.25, parsed))


def openai_speech(text, voice, fmt, api_key, model="gpt-4o-mini-tts", speed="1"):
    log_generation_request(
        "audio",
        "openai",
        "https://api.openai.com/v1/audio/speech",
        {
            "model": model or "gpt-4o-mini-tts",
            "voice": voice or "alloy",
            "format": fmt,
            "speed": normalize_tts_speed(speed),
            "input_preview": str(text or "")[:300],
        },
    )
    payload = json.dumps({
        "model": model or "gpt-4o-mini-tts",
        "input": text,
        "voice": voice or "alloy",
        "format": fmt,
        "speed": normalize_tts_speed(speed),
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _normalized_tts_local_base_url(raw_url):
    candidate = (raw_url or "").strip()
    if not candidate:
        candidate = os.getenv("KOKORO_BASE_URL", "http://127.0.0.1:8880")
    return candidate.rstrip("/")


def kokoro_speech(text, voice, fmt, base_url, model="kokoro", speed="1"):
    local_base_url = _normalized_tts_local_base_url(base_url)
    log_generation_request(
        "audio",
        "local/kokoro",
        f"{local_base_url}/v1/audio/speech",
        {
            "model": model or "kokoro",
            "voice": voice or "af_heart",
            "response_format": fmt,
            "speed": normalize_tts_speed(speed),
            "input_preview": str(text or "")[:300],
        },
    )
    payload = json.dumps({
        "model": model or "kokoro",
        "input": text,
        "voice": voice or "af_heart",
        "response_format": fmt,
        "speed": normalize_tts_speed(speed),
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{local_base_url}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "x-raw-response": "true"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
        content_type = (r.headers.get("Content-Type") or "").lower()
    if content_type.startswith("audio/") or fmt == "pcm":
        return body
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ValueError(f"Unexpected Kokoro response ({content_type or 'unknown'}).") from exc
    download_url = (parsed.get("download_url") or parsed.get("url") or "").strip()
    if download_url:
        req = urllib.request.Request(urllib.parse.urljoin(f"{local_base_url}/", download_url.lstrip("/")), method="GET")
        with urllib.request.urlopen(req, timeout=120) as audio_resp:
            return audio_resp.read()
    audio_b64 = parsed.get("audio_base64") or parsed.get("audio")
    if audio_b64:
        return base64.b64decode(audio_b64)
    raise ValueError("Kokoro response did not include audio bytes.")


def kokoro_list_voices(base_url):
    req = urllib.request.Request(f"{_normalized_tts_local_base_url(base_url)}/v1/audio/voices", method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    voices = []
    if isinstance(payload, list):
        voices = [str(v).strip() for v in payload]
    elif isinstance(payload, dict):
        for key in ("voices", "data", "items"):
            if isinstance(payload.get(key), list):
                voices = [str(v if isinstance(v, str) else (v.get("id") if isinstance(v, dict) else "")).strip() for v in payload[key]]
                break
    return sorted({v for v in voices if v})


def openai_stt(filepath, api_key, language="auto"):
    log_generation_request(
        "audio_transcription",
        "openai",
        "https://api.openai.com/v1/audio/transcriptions",
        {"language": language or "auto", "filename": Path(filepath).name},
    )
    boundary = "----NiceAssistantBoundary" + secrets.token_hex(8)
    with open(filepath, "rb") as f:
        audio = f.read()
    parts = []
    def add(name, value, filename=None, ctype="text/plain"):
        parts.append(f"--{boundary}\r\n".encode())
        if filename:
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
            parts.append(value)
            parts.append(b"\r\n")
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    add("model", "whisper-1")
    if language and language != "auto":
        add("language", str(language))
    add("file", audio, filename="audio.wav", ctype="audio/wav")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def openai_image(prompt, size, quality, api_key):
    safe_prompt = adjust_prompt_for_openai_image(prompt)
    normalized_quality = normalize_openai_image_quality(quality)
    log_generation_request(
        "image",
        "openai",
        "https://api.openai.com/v1/images/generations",
        {
            "model": "gpt-image-1",
            "size": size or "1024x1024",
            "quality": normalized_quality,
            "prompt_preview": safe_prompt[:300],
        },
    )
    payload = json.dumps({
        "model": "gpt-image-1",
        "prompt": safe_prompt,
        "size": size or "1024x1024",
        "quality": normalized_quality,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode())
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    image_url = item.get("url")
    if image_url:
        with urllib.request.urlopen(image_url, timeout=120) as image_resp:
            return image_resp.read()
    raise ValueError("Image response did not include data")



def _openai_auth_json_request(url, payload, api_key, timeout=180):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _openai_get_json(url, api_key, timeout=120):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _openai_get_bytes(url, api_key, timeout=300):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.headers.get("Content-Type") or "").lower()


def _extract_openai_video_output_url(payload):
    if isinstance(payload, dict):
        for key in ("url", "video_url", "output_video_url"):
            val = payload.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
        for container_key in ("data", "output", "result"):
            nested = payload.get(container_key)
            if isinstance(nested, list):
                for item in nested:
                    found = _extract_openai_video_output_url(item)
                    if found:
                        return found
            elif isinstance(nested, dict):
                found = _extract_openai_video_output_url(nested)
                if found:
                    return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_openai_video_output_url(item)
            if found:
                return found
    return ""


def openai_video(prompt, size, seconds, api_key, model="sora-2", input_reference=None):
    normalized_model = normalize_video_model(model)
    normalized_seconds = normalize_video_seconds(seconds)
    normalized_size = normalize_video_size(size, normalized_model)
    base_payload = {
        "model": normalized_model,
        "prompt": (prompt or "").strip(),
    }

    payload_attempts = [
        {**base_payload, "seconds": normalized_seconds, "size": normalized_size},
        {**base_payload, "seconds": normalized_seconds},
        {**base_payload, "size": normalized_size},
        base_payload,
    ]

    data = None
    last_exc = None
    for payload in payload_attempts:
        try:
            log_generation_request(
                "video",
                "openai",
                "https://api.openai.com/v1/videos",
                {
                    "model": payload.get("model"),
                    "seconds": payload.get("seconds"),
                    "size": payload.get("size"),
                    "prompt_preview": str(payload.get("prompt") or "")[:300],
                    "input_reference": bool(input_reference),
                },
            )
            if input_reference:
                field_values = {k: str(v) for k, v in payload.items()}
                data = _openai_auth_multipart_request(
                    "https://api.openai.com/v1/videos",
                    field_values,
                    file_field=input_reference,
                    api_key=api_key,
                    timeout=240,
                )
            else:
                data = _openai_auth_json_request("https://api.openai.com/v1/videos", payload, api_key, timeout=240)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                last_exc = exc
                continue
            raise
    if data is None and last_exc is not None:
        raise last_exc
    if data is None:
        raise ValueError("OpenAI video generation did not return a response payload")

    video_url = _extract_openai_video_output_url(data)
    video_id = data.get("id") if isinstance(data, dict) else None
    status = str((data or {}).get("status") or "").lower()

    if video_id and status in {"queued", "in_progress", "processing", "running", "pending", ""}:
        for _ in range(45):
            time.sleep(2)
            polled = _openai_get_json(f"https://api.openai.com/v1/videos/{video_id}", api_key, timeout=120)
            status = str((polled or {}).get("status") or "").lower()
            if not video_url:
                video_url = _extract_openai_video_output_url(polled)
            if status in {"failed", "cancelled", "canceled", "error"}:
                error_obj = (polled or {}).get("error") if isinstance(polled, dict) else None
                error_msg = ""
                if isinstance(error_obj, dict):
                    error_msg = str(error_obj.get("message") or "").strip()
                suffix = f": {error_msg}" if error_msg else ""
                raise ValueError(f"OpenAI video generation failed with status '{status}'{suffix}")
            if status in {"completed", "succeeded", "done"}:
                break

    if video_id and status in {"completed", "succeeded", "done"}:
        data, content_type = _openai_get_bytes(f"https://api.openai.com/v1/videos/{video_id}/content", api_key, timeout=300)
    elif video_url:
        with urllib.request.urlopen(video_url, timeout=300) as video_resp:
            data = video_resp.read()
            content_type = (video_resp.headers.get("Content-Type") or "").lower()
    else:
        raise ValueError("OpenAI video response did not include downloadable content")

    ext = ".mp4"
    if "webm" in content_type:
        ext = ".webm"
    elif "quicktime" in content_type:
        ext = ".mov"
    return data, ext


def normalize_video_model(model):
    candidate = (model or "").strip().lower()
    if candidate in SUPPORTED_VIDEO_MODELS:
        return candidate
    return "sora-2"


def normalize_video_seconds(seconds):
    candidate = str(seconds or "").strip()
    if candidate in SUPPORTED_VIDEO_SECONDS:
        return candidate
    return "4"


def normalize_video_size(size, model="sora-2"):
    candidate = (size or "").strip().lower()
    normalized_model = normalize_video_model(model)
    allowed_sizes = SUPPORTED_VIDEO_SIZES_BY_MODEL.get(normalized_model, SUPPORTED_VIDEO_SIZES_BY_MODEL["sora-2"])
    if candidate in allowed_sizes:
        return candidate
    if normalized_model == "sora-2-pro":
        return "1024x1792"
    return "720x1280"


def _openai_auth_multipart_request(url, fields, file_field, api_key, timeout=240):
    boundary = "----NiceAssistantBoundary" + secrets.token_hex(8)
    parts = []

    def add_field(name, value):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    for name, value in fields.items():
        add_field(name, value)

    if file_field:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="input_reference"; filename="{file_field.get("filename") or "reference.png"}"\r\n'.encode()
        )
        parts.append(f'Content-Type: {file_field.get("content_type") or "application/octet-stream"}\r\n\r\n'.encode())
        parts.append(file_field.get("value") or b"")
        parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def normalize_local_image_base_url(base_url):
    candidate = (base_url or "").strip() or AUTOMATIC1111_BASE_URL
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local image server URL must be a valid http(s) URL")
    return candidate.rstrip("/")


def normalize_local_image_backend(value):
    candidate = (value or "").strip().lower()
    if candidate in {"automatic1111", "comfyui"}:
        return candidate
    return "automatic1111"


def _coerce_number(value, default, cast_type=float):
    try:
        return cast_type(value)
    except (TypeError, ValueError):
        return default


def parse_additional_parameters(raw):
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("Additional Parameters must be valid JSON object text")
    if not isinstance(parsed, dict):
        raise ValueError("Additional Parameters must be a JSON object")
    return parsed


def _auth_headers_from_string(auth_string):
    raw = (auth_string or "").strip()
    if not raw:
        return {}
    token = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def automatic1111_image(prompt, size, quality, allow_nsfw, base_url=None, local_settings=None):
    local_settings = local_settings or {}
    width, height = parse_image_size(size, allow_custom=True)
    tuned_prompt = adjust_prompt_for_local_sd(prompt, allow_nsfw, quality)
    steps = int(_coerce_number(local_settings.get("steps"), local_steps_from_quality(quality), int))
    cfg_scale = _coerce_number(local_settings.get("cfg_scale"), 7.0, float)
    seed = int(_coerce_number(local_settings.get("seed"), -1, int))
    sampler_name = (local_settings.get("sampler_name") or "DPM++ 2M Karras").strip()
    scheduler = (local_settings.get("scheduler") or "").strip()
    model_checkpoint = (local_settings.get("model") or "").strip()
    payload = {
        "prompt": tuned_prompt,
        "negative_prompt": local_negative_prompt(allow_nsfw, quality),
        "width": width,
        "height": height,
        "steps": max(1, steps),
        "cfg_scale": max(1.0, cfg_scale),
        "sampler_name": sampler_name,
        "seed": seed,
    }
    if scheduler:
        payload["scheduler"] = scheduler
    if model_checkpoint:
        payload["override_settings"] = {"sd_model_checkpoint": model_checkpoint}
    payload.update(parse_additional_parameters(local_settings.get("additional_parameters")))
    request_base_url = normalize_local_image_base_url(base_url)
    log_generation_request(
        "image",
        "local/automatic1111",
        f"{request_base_url}/sdapi/v1/txt2img",
        {
            "width": payload.get("width"),
            "height": payload.get("height"),
            "steps": payload.get("steps"),
            "cfg_scale": payload.get("cfg_scale"),
            "sampler_name": payload.get("sampler_name"),
            "scheduler": payload.get("scheduler", ""),
            "seed": payload.get("seed"),
            "prompt_preview": str(payload.get("prompt") or "")[:300],
            "negative_prompt_preview": str(payload.get("negative_prompt") or "")[:200],
        },
    )
    req = urllib.request.Request(
        f"{request_base_url}/sdapi/v1/txt2img",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **_auth_headers_from_string(local_settings.get("api_auth"))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(r.read().decode())
    images = data.get("images") or []
    if not images:
        raise ValueError("Automatic1111 image response did not include data")
    return base64.b64decode(images[0])


def comfyui_image(prompt, size, quality, allow_nsfw, base_url=None, local_settings=None):
    local_settings = local_settings or {}
    width, height = parse_image_size(size, allow_custom=True)
    tuned_prompt = adjust_prompt_for_local_sd(prompt, allow_nsfw, quality)
    steps = int(_coerce_number(local_settings.get("steps"), local_steps_from_quality(quality), int))
    cfg_scale = _coerce_number(local_settings.get("cfg_scale"), 7.0, float)
    seed = local_seed_for_backend(local_settings.get("seed"), "comfyui")
    sampler_name = (local_settings.get("sampler_name") or "euler").strip()
    scheduler = (local_settings.get("scheduler") or "normal").strip()
    model_checkpoint = (local_settings.get("model") or "v1-5-pruned-emaonly.safetensors").strip()
    negative_prompt = local_negative_prompt(allow_nsfw, quality)
    request_base_url = normalize_local_image_base_url((base_url or "").strip() or COMFYUI_BASE_URL)

    workflow = {
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": max(1, steps), "cfg": max(1.0, cfg_scale), "sampler_name": sampler_name, "scheduler": scheduler, "denoise": 1, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model_checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": tuned_prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "nice-assistant", "images": ["8", 0]}},
    }
    workflow.update(parse_additional_parameters(local_settings.get("additional_parameters")))
    payload = {"prompt": workflow, "client_id": f"nice-assistant-{secrets.token_hex(8)}"}
    log_generation_request(
        "image",
        "local/comfyui",
        f"{request_base_url}/prompt",
        {
            "width": width,
            "height": height,
            "steps": max(1, steps),
            "cfg_scale": max(1.0, cfg_scale),
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "seed": seed,
            "model": model_checkpoint,
            "prompt_preview": tuned_prompt[:300],
            "negative_prompt_preview": negative_prompt[:200],
            "client_id": payload["client_id"],
        },
    )

    req = urllib.request.Request(
        f"{request_base_url}/prompt",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **_auth_headers_from_string(local_settings.get("api_auth"))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    prompt_id = (data or {}).get("prompt_id")
    if not prompt_id:
        raise ValueError("ComfyUI did not return a prompt_id")

    history = None
    for _ in range(120):
        hist_req = urllib.request.Request(
            f"{request_base_url}/history/{urllib.parse.quote(str(prompt_id))}",
            headers=_auth_headers_from_string(local_settings.get("api_auth")),
            method="GET",
        )
        with urllib.request.urlopen(hist_req, timeout=30) as r:
            history_data = json.loads(r.read().decode())
        if history_data:
            history = history_data
            break
        time.sleep(1)
    if not history:
        raise TimeoutError("ComfyUI history polling timed out")

    node_outputs = ((history.get(str(prompt_id)) or {}).get("outputs") or {})
    image_item = None
    for output in node_outputs.values():
        images = output.get("images") or []
        if images:
            image_item = images[0]
            break
    if not image_item:
        raise ValueError("ComfyUI completed without returning image output")

    query = urllib.parse.urlencode(
        {
            "filename": image_item.get("filename", ""),
            "subfolder": image_item.get("subfolder", ""),
            "type": image_item.get("type", "output"),
        }
    )
    view_req = urllib.request.Request(
        f"{request_base_url}/view?{query}",
        headers=_auth_headers_from_string(local_settings.get("api_auth")),
        method="GET",
    )
    with urllib.request.urlopen(view_req, timeout=120) as r:
        return r.read()


def normalize_image_quality(quality):
    normalized = IMAGE_QUALITY_ALIASES.get(quality, quality)
    if normalized in IMAGE_QUALITY_VALUES:
        return normalized
    return "auto"


def normalize_openai_image_quality(quality):
    normalized = normalize_image_quality(quality)
    if normalized == "none":
        return "auto"
    return normalized


def normalize_image_size(size):
    if size in SUPPORTED_IMAGE_SIZES:
        return size
    return "1024x1024"


def parse_image_size(size, allow_custom=False):
    raw = (size or "").strip().lower()
    if allow_custom and raw:
        custom_match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", raw)
        if custom_match:
            return int(custom_match.group(1)), int(custom_match.group(2))
    normalized = normalize_image_size(raw)
    if normalized == "auto":
        return 1024, 1024
    try:
        width, height = normalized.split("x", 1)
        return int(width), int(height)
    except Exception:
        return 1024, 1024


def clean_user_image_prompt(prompt):
    text = " ".join((prompt or "").split()).strip()
    if not text:
        return ""
    prefixes = [
        r"^(please\s+)?(can you\s+)?(generate|create|make|draw|render|produce)\s+(me\s+)?(an?|the)?\s*(image|picture|photo|illustration|artwork)?\s*(of|with)?\s+",
        r"^(please\s+)?(show|give)\s+me\s+(an?|the)?\s*(image|picture|photo|illustration)\s*(of|with)?\s+",
    ]
    cleaned = text
    for pattern in prefixes:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^an?\s+image\s+of\s+", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^(the\s+following\s+prompt\s*:?\s*)", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^(prompt\s*:?\s*)", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    return cleaned or text


def adjust_prompt_for_openai_image(prompt):
    text = clean_user_image_prompt(prompt)
    if not text:
        return "Generate a polished, policy-compliant image suitable for general audiences."
    adjusted = text
    for term, replacement in OPENAI_IMAGE_TERM_REPLACEMENTS.items():
        adjusted = re.sub(rf"\b{re.escape(term)}\b", replacement, adjusted, flags=re.IGNORECASE)
    return (
        "Generate a polished, coherent image with clear subject emphasis, intentional composition, and rich lighting detail. "
        f"Scene request: {adjusted}. "
        "Keep it policy-compliant and suitable for general audiences."
    )


def local_steps_from_quality(quality):
    normalized = normalize_image_quality(quality)
    if normalized == "high":
        return 38
    if normalized == "low":
        return 20
    return 28


def local_negative_prompt(allow_nsfw, quality="auto"):
    if normalize_image_quality(quality) == "none":
        return ""
    base = "blurry, lowres, jpeg artifacts, extra limbs, deformed hands, bad anatomy, watermark, text, logo"
    if allow_nsfw:
        return base
    return f"{base}, nude, nudity, explicit sexual content, fetish, porn, graphic violence, gore"


def adjust_prompt_for_local_sd(prompt, allow_nsfw, quality="auto"):
    text = clean_user_image_prompt(prompt)
    if not text:
        text = "cinematic portrait of a friendly assistant in a modern workspace, detailed lighting, highly detailed"
    if not allow_nsfw:
        for term, replacement in OPENAI_IMAGE_TERM_REPLACEMENTS.items():
            text = re.sub(rf"\b{re.escape(term)}\b", replacement, text, flags=re.IGNORECASE)
    if normalize_image_quality(quality) == "none":
        return text
    return f"masterpiece, best quality, highly detailed, {text}"


def local_seed_for_backend(seed_value, backend):
    normalized_backend = normalize_local_image_backend(backend)
    if normalized_backend == "comfyui":
        seed_raw = str(seed_value or "").strip()
        if seed_raw in {"", "-1"}:
            return secrets.randbelow(2**63 - 1) + 1
        return int(_coerce_number(seed_value, secrets.randbelow(2**63 - 1) + 1, int))
    return int(_coerce_number(seed_value, -1, int))


def image_prompt_is_detailed(prompt):
    words = re.findall(r"[A-Za-z0-9']+", prompt or "")
    if len(words) < 12:
        return False
    checks = [
        re.search(r"\b(shot|close-up|wide|angle|composition|framing|portrait|landscape)\b", prompt, re.IGNORECASE),
        re.search(r"\b(light|lighting|sunset|neon|moody|dramatic|soft light)\b", prompt, re.IGNORECASE),
        re.search(r"\b(style|illustration|photo|cinematic|render|painting|anime|realistic)\b", prompt, re.IGNORECASE),
    ]
    return sum(bool(c) for c in checks) >= 1


def model_image_instruction_for_provider(provider, local_backend="automatic1111"):
    provider = (provider or "disabled").lower().strip()
    base = (
        "When a user clearly asks for an image, include exactly one XML tag like "
        "<generate_image>...</generate_image> in your reply. "
        "Inside the tag, provide a production-quality prompt with subject, environment, composition/camera, "
        "lighting, style, and quality details. Keep it safe and policy-compliant. "
        "If the image includes the user or assistant persona, preserve known visual continuity from chat memory/persona settings "
        "(for example avatar cues, physical traits, and wardrobe style) and do not invent sensitive physical details that are unknown."
    )
    if provider == "openai":
        return (
            f"{base} Tailor prompt style for OpenAI image generation: natural-language scene direction, clear visual intent, "
            "minimal comma-stuffing, and no tool-specific weight syntax."
        )
    if provider == "local":
        backend = normalize_local_image_backend(local_backend)
        if backend == "comfyui":
            return (
                f"{base} Tailor prompt style for ComfyUI/Stable Diffusion pipelines: concise keyword-rich descriptors, "
                "clear art direction tokens, and maintain compatibility with a separate negative prompt/workflow nodes."
            )
        return (
            f"{base} Tailor prompt style for Stable Diffusion/Automatic1111: concise keyword-rich descriptors, "
            "art direction tokens, and include details that pair well with a separate negative prompt."
        )
    return base


def visual_identity_context(conn, uid, chat_id, persona_row, workspace_id=None, persona_id=None):
    cues = []
    if persona_row:
        traits = parse_traits(persona_row["traits_json"])
        persona_bits = [f"assistant persona is '{persona_row['name']}'"]
        if traits["gender"] == "other" and traits["gender_other"]:
            persona_bits.append(f"gender: {traits['gender_other']}")
        elif traits["gender"] != "unspecified":
            persona_bits.append(f"gender: {traits['gender']}")
        if traits["age"]:
            persona_bits.append(f"age: {traits['age']}")
        if persona_row["personality_details"]:
            persona_bits.append(f"persona profile: {persona_row['personality_details'][:180]}")
        cues.append("; ".join(persona_bits))

    if conn and uid:
        rows = []
        if chat_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='chat' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, chat_id),
                ).fetchall()
            )
        if persona_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, persona_id),
                ).fetchall()
            )
        if workspace_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, workspace_id),
                ).fetchall()
            )
        if chat_id:
            chat_rows = conn.execute(
                "SELECT text AS content FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 20",
                (chat_id,),
            ).fetchall()
            rows.extend(chat_rows)
        pat = re.compile(r"\b(my|i am|i'm|look like|appearance|hair|eyes|face|skin|height|wear|wearing|outfit|avatar)\b", re.IGNORECASE)
        seen = set()
        for row in rows:
            content = " ".join(str(row[0] or "").split())
            if len(content) < 8 or not pat.search(content):
                continue
            lowered = content.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cues.append(content[:180])
            if len(cues) >= 6:
                break

    if not cues:
        return ""
    return (
        "Visual continuity constraints: "
        + " | ".join(cues)
        + " If details are missing, keep identity descriptors neutral instead of guessing."
    )


def _extract_http_error_detail(exc):
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
        if not body:
            return ""
        parsed = json.loads(body)
        return ((parsed.get("error") or {}).get("message") or parsed.get("message") or body).strip()
    except Exception:
        try:
            return body.strip()
        except Exception:
            return ""


def log_image_error(uid, chat_id, detail):
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = LOG_DIR / f"image_generation_{stamp}.log"
        with target.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} user={uid} chat={chat_id} detail={detail}\n")
    except Exception:
        logger.exception("failed to write image error log")


def looks_like_image_request(text):
    if not text:
        return False
    lowered = " ".join(text.lower().split())
    verbs = ("generate", "create", "make", "draw", "render")
    nouns = ("image", "picture", "photo", "illustration", "art")
    has_verb = any(v in lowered for v in verbs)
    has_noun = any(n in lowered for n in nouns)
    return has_verb and has_noun


def looks_like_video_request(text):
    if not text:
        return False
    lowered = " ".join(text.lower().split())
    verbs = ("generate", "create", "make", "render", "produce")
    nouns = ("video", "clip", "animation", "movie", "footage")
    has_verb = any(v in lowered for v in verbs)
    has_noun = any(n in lowered for n in nouns)
    return has_verb and has_noun


def user_safe_image_error(exc, provider="openai"):
    provider = (provider or "openai").strip().lower()
    detail = _extract_http_error_detail(exc)
    req_match = re.search(r"(req_[a-zA-Z0-9]+)", detail or "")
    req_id = req_match.group(1) if req_match else ""
    if "safety" in (detail or "").lower() or "safety_violations" in (detail or "").lower():
        return "I couldn't generate that image because the request was flagged by safety filters. Please try a safer rewording.", detail, req_id
    if provider == "openai" and "Supported values are" in (detail or "") and "Invalid value" in (detail or ""):
        return "That image size is not supported by OpenAI. Please choose 1024x1024, 1024x1536, 1536x1024, or auto.", detail, req_id
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if provider in {"local", "local/automatic1111"}:
            if status in (401, 403):
                return "Image generation failed: Automatic1111 rejected authentication. Check the API auth string and --api-auth setup.", detail, req_id
            if status == 404:
                return "Automatic1111 API endpoint not found. Ensure webui is running with the --api flag.", detail, req_id
            if status == 422:
                return "Automatic1111 rejected one or more generation parameters. Check image size/steps/sampler/scheduler values.", detail, req_id
            if 500 <= status <= 599:
                return "Automatic1111 failed while generating the image. Please retry after the server is ready.", detail, req_id
            return f"Image generation failed with Automatic1111 HTTP {status}.", detail, req_id
        if provider == "local/comfyui":
            if status in (401, 403):
                return "Image generation failed: ComfyUI rejected authentication. Check your API auth settings.", detail, req_id
            if status == 404:
                return "ComfyUI route not found. Verify the ComfyUI server URL and that the server is running.", detail, req_id
            if status == 422:
                return "ComfyUI rejected one or more workflow parameters. Check model, sampler/scheduler, and additional JSON.", detail, req_id
            if 500 <= status <= 599:
                return "ComfyUI failed while generating the image. Please retry after the server is ready.", detail, req_id
            return f"Image generation failed with ComfyUI HTTP {status}.", detail, req_id
        if status in (401, 403):
            return "Image generation failed: OpenAI rejected the request (check API key and image model access).", detail, req_id
        if status == 429:
            return "Image generation is rate limited by OpenAI right now. Please retry in a moment.", detail, req_id
        if status in (400, 404):
            return "OpenAI couldn't generate that image from the current request. Please revise the prompt and try again.", detail, req_id
        if 500 <= status <= 599:
            return "Image generation is temporarily unavailable from OpenAI. Please try again shortly.", detail, req_id
        return f"Image generation failed with OpenAI error HTTP {status}.", detail, req_id
    if isinstance(exc, urllib.error.URLError):
        if provider in {"local", "local/automatic1111"}:
            return "Image generation is currently unavailable because Automatic1111 could not be reached. Check URL and that webui is running with --api.", detail, req_id
        if provider == "local/comfyui":
            return "Image generation is currently unavailable because ComfyUI could not be reached. Check URL and that the ComfyUI server is running.", detail, req_id
        return "Image generation is currently unavailable because the image provider could not be reached. Please try again in a moment.", detail, req_id
    if isinstance(exc, TimeoutError):
        return "Image generation timed out. Please try again with a shorter prompt or retry shortly.", detail, req_id
    return "Image generation failed unexpectedly. Please try again.", detail, req_id



def user_safe_video_error(exc):
    detail = _extract_http_error_detail(exc)
    req_match = re.search(r"(req_[a-zA-Z0-9]+)", detail or "")
    req_id = req_match.group(1) if req_match else ""
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if status in (401, 403):
            return "Video generation failed: OpenAI rejected the request (check API key and video model access).", detail, req_id
        if status == 429:
            return "Video generation is rate limited by OpenAI right now. Please retry in a moment.", detail, req_id
        if status in (400, 404, 422):
            if detail:
                return (
                    "OpenAI rejected the video request settings or payload. "
                    "Try video model 'sora-2' with 4 seconds and size 720x1280 or 1280x720, then retry."
                ), detail, req_id
            return "OpenAI couldn't generate that video from the current request. Please revise the prompt and try again.", detail, req_id
        if 500 <= status <= 599:
            return "Video generation is temporarily unavailable from OpenAI. Please try again shortly.", detail, req_id
        return f"Video generation failed with OpenAI error HTTP {status}.", detail, req_id
    if isinstance(exc, urllib.error.URLError):
        return "Video generation is currently unavailable because the OpenAI provider could not be reached. Please try again in a moment.", detail, req_id
    if isinstance(exc, TimeoutError):
        return "Video generation timed out. Please try again with a shorter prompt or retry shortly.", detail, req_id
    return "Video generation failed unexpectedly. Please try again.", detail, req_id

def extract_model_image_prompt(reply_text):
    if not reply_text:
        return "", ""
    match = MODEL_IMAGE_TAG_PATTERN.search(reply_text)
    if not match:
        return reply_text, ""
    prompt = " ".join(match.group(1).split()).strip()
    clean_reply = MODEL_IMAGE_TAG_PATTERN.sub("", reply_text).strip()
    return clean_reply, prompt


def generate_image_reply(prompt, uid, chat_id, settings_row, prefs, context_hint=""):
    image_provider = (prefs or {}).get("image_provider", "disabled")
    image_local_backend_override = None
    if image_provider == "local/automatic1111":
        image_provider = "local"
    if image_provider == "local/comfyui":
        image_provider = "local"
        image_local_backend_override = "comfyui"
    if image_provider == "disabled":
        return "I can generate images, but image generation is currently disabled. Enable an image provider in Settings and try again.", ""
    image_size = (prefs or {}).get("image_size") or "1024x1024"
    image_quality = (prefs or {}).get("image_quality") or "standard"
    image_local_allow_nsfw = bool((prefs or {}).get("image_local_allow_nsfw", False))
    image_local_base_url = (prefs or {}).get("image_local_base_url")
    image_local_backend = normalize_local_image_backend(image_local_backend_override or (prefs or {}).get("image_local_backend"))
    image_id = secrets.token_hex(12)
    image_ext = "png"
    image_name = f"{uid}_{image_id}.{image_ext}"
    image_path = IMAGE_DIR / image_name
    try:
        effective_prompt = f"{prompt}\n\n{context_hint}".strip() if context_hint else prompt
        if image_provider == "openai":
            key = settings_row["openai_api_key"] if settings_row else None
            if not key:
                return "Image generation is enabled for OpenAI, but your OpenAI API key is missing in Settings.", ""
            image_size = normalize_image_size(image_size)
            image_bytes = openai_image(effective_prompt, image_size, image_quality, key)
        elif image_provider == "local":
            MODEL_RESIDENCY.update_policy(**parse_residency_policy_preferences(prefs))
            local_settings = {
                "steps": (prefs or {}).get("image_local_steps"),
                "cfg_scale": (prefs or {}).get("image_local_cfg_scale"),
                "seed": (prefs or {}).get("image_local_seed"),
                "sampler_name": (prefs or {}).get("image_local_sampler_name"),
                "scheduler": (prefs or {}).get("image_local_scheduler"),
                "model": (prefs or {}).get("image_local_model"),
                "api_auth": (prefs or {}).get("image_local_api_auth"),
                "additional_parameters": (prefs or {}).get("image_local_additional_parameters"),
            }
            local_model_id = str(local_settings.get("model") or image_local_backend)
            MODEL_RESIDENCY.ensure_loaded("image_local", IMAGE_ESTIMATED_VRAM_MB, model_id=local_model_id)
            if image_local_backend == "comfyui":
                image_bytes = comfyui_image(effective_prompt, image_size, image_quality, image_local_allow_nsfw, image_local_base_url, local_settings=local_settings)
            else:
                image_bytes = automatic1111_image(effective_prompt, image_size, image_quality, image_local_allow_nsfw, image_local_base_url, local_settings=local_settings)
        else:
            return f"Image provider '{image_provider}' is not recognized by the server. Choose 'openai' or 'local'.", ""
        image_path.write_bytes(image_bytes)
        record_media_file(uid, chat_id, "image", image_name, image_path)
        image_url = f"/api/images/{urllib.parse.quote(image_name)}"
        return f"Here is your generated image.\n\n![Generated image]({image_url})", image_url
    except Exception as exc:
        if isinstance(exc, MemoryBackpressureError):
            logger.info(
                "image generation backpressure user_id=%s chat_id=%s detail=%s",
                uid,
                chat_id,
                json.dumps(exc.details, sort_keys=True),
            )
            return exc.user_message, ""
        logger.exception("image generation failed user_id=%s chat_id=%s", uid, chat_id)
        resolved_provider = image_provider
        if image_provider == "local":
            resolved_provider = f"local/{image_local_backend}"
        message, detail, req_id = user_safe_image_error(exc, provider=resolved_provider)
        if detail:
            log_image_error(uid, chat_id, f"request_id={req_id or 'n/a'} {detail}")
        return message, ""




def generate_video_reply(prompt, uid, chat_id, settings_row, prefs, input_reference=None):
    video_provider = ((prefs or {}).get("video_provider") or "disabled").strip().lower()
    if video_provider == "disabled":
        return "I can generate videos, but video generation is currently disabled. Enable a video provider in Settings and try again.", ""
    if video_provider != "openai":
        return f"Video provider '{video_provider}' is not recognized by the server. Choose 'openai'.", ""

    key = settings_row["openai_api_key"] if settings_row else None
    if not key:
        return "Video generation is enabled for OpenAI, but your OpenAI API key is missing in Settings.", ""

    video_model = normalize_video_model((prefs or {}).get("video_model") or "sora-2")
    video_size = normalize_video_size((prefs or {}).get("video_size") or "720x1280", video_model)
    video_duration = normalize_video_seconds((prefs or {}).get("video_duration") or "4")

    video_id = secrets.token_hex(12)
    try:
        video_bytes, ext = openai_video(prompt, video_size, video_duration, key, model=video_model, input_reference=input_reference)
        safe_ext = ext if ext in {".mp4", ".webm", ".mov"} else ".mp4"
        video_name = f"{uid}_{video_id}{safe_ext}"
        video_path = VIDEO_DIR / video_name
        video_path.write_bytes(video_bytes)
        record_media_file(uid, chat_id, "video", video_name, video_path)
        video_url = f"/api/videos/{urllib.parse.quote(video_name)}"
        return f"Here is your generated video.\n\n[Download generated video]({video_url})", video_url
    except Exception as exc:
        logger.exception("video generation failed user_id=%s chat_id=%s", uid, chat_id)
        message, detail, req_id = user_safe_video_error(exc)
        if detail:
            log_image_error(uid, chat_id, f"video request_id={req_id or 'n/a'} {detail}")
        return message, ""

def parse_preferences_json(raw_value):
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def parse_residency_policy_preferences(prefs):
    policy = {}
    if not isinstance(prefs, dict):
        return policy
    mapping = {
        "gpu_idle_hold_seconds_llm": float,
        "gpu_idle_hold_seconds_image": float,
        "gpu_min_residency_seconds": float,
        "max_model_swaps_per_minute": int,
        "queue_affinity_window_ms": int,
    }
    for key, caster in mapping.items():
        value = prefs.get(key)
        if value is None or value == "":
            continue
        try:
            converted = caster(value)
        except (TypeError, ValueError):
            continue
        if caster is float:
            policy[key] = max(0.0, converted)
        else:
            policy[key] = max(0, converted)
    return policy


def setting_bool(settings_row, key, default=False):
    prefs = parse_preferences_json(settings_row["preferences_json"] if settings_row else "{}")
    val = prefs.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def log_interaction(event_type, message, **context):
    context_items = " ".join([f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(context.items()) if v is not None])
    payload = f"event={event_type} message={message}"
    if context_items:
        payload = f"{payload} {context_items}"
    logger.info(payload)


def safe_name(name, fallback):
    candidate = (name or "").strip().replace(" ", "_")
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", candidate)
    return candidate or fallback


def load_user_settings_and_prefs(uid):
    conn = db_conn()
    settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return settings, parse_preferences_json(settings["preferences_json"] if settings else "{}")


def user_workspace_row(conn, uid, workspace_id):
    if not workspace_id:
        return None
    return conn.execute("SELECT * FROM workspaces WHERE id=? AND user_id=?", (workspace_id, uid)).fetchone()


def user_persona_row(conn, uid, persona_id):
    if not persona_id:
        return None
    return conn.execute(
        """
        SELECT p.*
        FROM personas p
        WHERE p.id=?
          AND EXISTS (
              SELECT 1
              FROM persona_workspace_links l
              JOIN workspaces w ON w.id=l.workspace_id
              WHERE l.persona_id=p.id AND w.user_id=?
          )
        """,
        (persona_id, uid),
    ).fetchone()


def validate_chat_scope(conn, uid, workspace_id=None, persona_id=None):
    persona = user_persona_row(conn, uid, persona_id) if persona_id else None
    if persona_id and not persona:
        raise ValueError("persona not found")
    if workspace_id and not user_workspace_row(conn, uid, workspace_id):
        raise ValueError("workspace not found")
    if workspace_id and persona:
        linked = conn.execute(
            "SELECT 1 FROM persona_workspace_links WHERE persona_id=? AND workspace_id=?",
            (persona_id, workspace_id),
        ).fetchone()
        if not linked:
            raise ValueError("persona not found")
    if not workspace_id and persona:
        workspace_id = persona["workspace_id"]
    return workspace_id, persona


def execute_image_generation_request(uid, payload, cancel_requested=None):
    b = dict(payload or {})
    prompt = str(b.get("prompt") or "").strip()
    chat_id = b.get("chatId")
    if not prompt:
        raise ValueError("prompt required")

    conn = db_conn()
    settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone()
    prefs = parse_preferences_json(settings["preferences_json"] if settings else "{}")
    persona_row = None
    if chat_id:
        chat = conn.execute("SELECT workspace_id, persona_id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
        if chat and chat["persona_id"]:
            persona_row = conn.execute("SELECT * FROM personas WHERE id=?", (chat["persona_id"],)).fetchone()
    use_context_hint = bool(b.get("useContextHint", False))
    context_hint = ""
    if use_context_hint:
        workspace_id = None
        persona_id = None
        if chat_id:
            chat = conn.execute("SELECT workspace_id, persona_id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
            if chat:
                workspace_id = chat["workspace_id"]
                persona_id = chat["persona_id"]
        context_hint = visual_identity_context(conn, uid, chat_id, persona_row, workspace_id=workspace_id, persona_id=persona_id)
    conn.close()

    if cancel_requested and cancel_requested():
        return {"ok": False, "text": "Request cancelled.", "chatId": chat_id}

    reply, image_url = generate_image_reply(prompt, uid, chat_id, settings, prefs, context_hint=context_hint)

    if cancel_requested and cancel_requested():
        return {"ok": False, "text": "Request cancelled.", "chatId": chat_id}

    if image_url and chat_id:
        conn = db_conn()
        owns = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
        if owns:
            conn.execute(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                (secrets.token_hex(8), chat_id, "assistant", reply, now_ts()),
            )
            conn.execute("UPDATE chats SET updated_at=? WHERE id=?", (now_ts(), chat_id))
            conn.commit()
        conn.close()
    if image_url:
        return {"ok": True, "text": reply, "imageUrl": image_url, "chatId": chat_id}
    return {"ok": False, "text": reply, "chatId": chat_id}


def execute_video_generation_request(uid, prompt, chat_id, input_reference=None, cancel_requested=None):
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    settings, prefs = load_user_settings_and_prefs(uid)

    if cancel_requested and cancel_requested():
        return {"ok": False, "text": "Request cancelled.", "chatId": chat_id}

    reply, video_url = generate_video_reply(prompt, uid, chat_id, settings, prefs, input_reference=input_reference)

    if cancel_requested and cancel_requested():
        return {"ok": False, "text": "Request cancelled.", "chatId": chat_id}

    if video_url and chat_id:
        conn = db_conn()
        owns = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
        if owns:
            conn.execute(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                (secrets.token_hex(8), chat_id, "assistant", reply, now_ts()),
            )
            conn.execute("UPDATE chats SET updated_at=? WHERE id=?", (now_ts(), chat_id))
            conn.commit()
        conn.close()
    if video_url:
        return {"ok": True, "text": reply, "videoUrl": video_url, "chatId": chat_id}
    return {"ok": False, "text": reply, "chatId": chat_id}


def prepare_chat_generation_request(uid, payload, text):
    b = dict(payload or {})
    chat_id = b.get("chatId")
    conn = db_conn()
    chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone() if chat_id else None
    if not chat:
        chat_id = secrets.token_hex(8)
        t = now_ts()
        workspace_id, _persona = validate_chat_scope(conn, uid, b.get("workspaceId"), b.get("personaId"))
        conn.execute(
            "INSERT INTO chats(id,user_id,workspace_id,persona_id,model_override,memory_mode,title,hidden_in_ui,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                uid,
                workspace_id,
                b.get("personaId"),
                b.get("model"),
                b.get("memoryMode", "auto"),
                generate_chat_title(text),
                0,
                t,
                t,
            ),
        )
        conn.commit()
    conn.close()
    return chat_id


def persist_chat_assistant_result(uid, chat_id, reply, mem_mode, persona_id, workspace_id, model_override, user_text, remember_short_facts=False):
    conn = db_conn()
    conn.execute(
        "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
        (secrets.token_hex(8), chat_id, "assistant", reply, now_ts()),
    )
    conn.execute(
        "UPDATE chats SET updated_at=?, memory_mode=?, persona_id=?, workspace_id=?, model_override=? WHERE id=?",
        (now_ts(), mem_mode, persona_id, workspace_id, model_override, chat_id),
    )
    if mem_mode == "auto":
        if remember_short_facts and len(user_text) < 280 and any(k in user_text.lower() for k in ["my ", "i like", "remember", "name is"]):
            conn.execute(
                "INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)",
                (secrets.token_hex(8), uid, "persona" if persona_id else "global", persona_id, user_text, now_ts()),
            )
        conn.execute(
            "INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)",
            (secrets.token_hex(8), uid, "chat", chat_id, user_text, now_ts()),
        )
    conn.commit()
    conn.close()
    backup_db_if_needed()


def run_chat_video_generation(text, uid, chat_id, settings, prefs, queue_provider):
    if not queue_provider:
        return generate_video_reply(text, uid, chat_id, settings, prefs)
    video_job = new_job(
        job_type="video",
        user_id=uid,
        chat_id=chat_id,
        estimated_vram_mb=VIDEO_ESTIMATED_VRAM_MB,
        latency_class="bulk",
        model_key=f"video:{(prefs or {}).get('video_model', '')}",
        metadata={"endpoint": "/api/chat"},
        execute=lambda: generate_video_reply(text, uid, chat_id, settings, prefs),
    )
    return JOB_QUEUE.submit(video_job).wait()


def run_chat_image_generation(text, uid, chat_id, settings, prefs, model, queue_provider):
    if not queue_provider:
        reply, image_url = generate_image_reply(text, uid, chat_id, settings, prefs, context_hint="")
        return "Generating your image now.", reply, image_url
    grouped_jobs = JOB_QUEUE.submit_group([
        new_job(
            job_type="text",
            user_id=uid,
            chat_id=chat_id,
            estimated_vram_mb=LLM_ESTIMATED_VRAM_MB // 8,
            latency_class="interactive",
            model_key=f"text:{model}",
            metadata={"endpoint": "/api/chat", "kind": "grouped_text"},
            execute=lambda: "Generating your image now.",
        ),
        new_job(
            job_type="image",
            user_id=uid,
            chat_id=chat_id,
            estimated_vram_mb=IMAGE_ESTIMATED_VRAM_MB,
            latency_class="standard",
            model_key=f"image:{(prefs or {}).get('image_provider', 'disabled')}",
            metadata={"endpoint": "/api/chat", "kind": "grouped_image"},
            execute=lambda: generate_image_reply(text, uid, chat_id, settings, prefs, context_hint=""),
        ),
    ])
    text_preface = grouped_jobs[0].wait()
    reply, image_url = grouped_jobs[1].wait()
    return text_preface, reply, image_url


def run_chat_model_generation(model, messages, model_options, prefs, uid, chat_id, queue_provider):
    if not queue_provider:
        return execute_chat_model_job(model, messages, model_options, prefs=prefs)
    llm_job = new_job(
        job_type="chat",
        user_id=uid,
        chat_id=chat_id,
        estimated_vram_mb=LLM_ESTIMATED_VRAM_MB,
        latency_class="interactive",
        model_key=f"chat:{model}",
        metadata={"endpoint": "/api/chat"},
        execute=lambda: execute_chat_model_job(model, messages, model_options, prefs=prefs),
    )
    return JOB_QUEUE.submit(llm_job).wait()


def execute_chat_generation_request(uid, payload, chat_id, queue_provider=True, cancel_requested=None):
    b = dict(payload or {})
    text = str(b.get("text") or "").strip()
    if not text:
        raise ValueError("text required")

    conn = db_conn()
    t = now_ts()
    chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
    if not chat:
        conn.close()
        raise ValueError("chat not found")
    mem_mode = b.get("memoryMode") or chat["memory_mode"] or "auto"
    persona_id = b.get("personaId") or chat["persona_id"]
    requested_workspace_id = b.get("workspaceId")
    workspace_id, persona = validate_chat_scope(conn, uid, requested_workspace_id or chat["workspace_id"], persona_id)
    workspace_id = workspace_id or (persona["workspace_id"] if persona else None)
    model = b.get("model") or chat["model_override"]
    settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone()
    if not model and persona_id:
        p = conn.execute("SELECT default_model FROM personas WHERE id=?", (persona_id,)).fetchone()
        model = p["default_model"] if p else None
    model = model or (settings["global_default_model"] if settings else None) or (ollama_models()[0] if ollama_models() else "llama3")
    model_options = parse_model_options(b.get("modelSettings") or {})
    logger.info(
        "chat request user_id=%s chat_id=%s model=%s memory_mode=%s persona_id=%s options=%s",
        uid,
        chat_id,
        model,
        mem_mode,
        persona_id,
        json.dumps(model_options, sort_keys=True),
    )

    conn.execute(
        "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
        (secrets.token_hex(8), chat_id, "user", text, t),
    )
    current_title_row = conn.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
    current_title = current_title_row["title"] if current_title_row else None
    if chat_title_needs_autogeneration(current_title):
        generated_title = generate_chat_title_from_first_user_message(text)
        conn.execute("UPDATE chats SET title=?, updated_at=? WHERE id=?", (generated_title, now_ts(), chat_id))
    else:
        conn.execute("UPDATE chats SET updated_at=? WHERE id=?", (now_ts(), chat_id))
    prefs = parse_preferences_json(settings["preferences_json"] if settings else "{}")

    if looks_like_video_request(text):
        conn.commit()
        conn.close()
        if cancel_requested and cancel_requested():
            return {"text": "Request cancelled.", "chatId": chat_id}
        reply, _video_url = run_chat_video_generation(text, uid, chat_id, settings, prefs, queue_provider)
        if cancel_requested and cancel_requested():
            return {"text": "Request cancelled.", "chatId": chat_id}
        persist_chat_assistant_result(uid, chat_id, reply, mem_mode, persona_id, workspace_id, b.get("model") or chat["model_override"], text)
        return {"text": reply, "chatId": chat_id}

    if looks_like_image_request(text):
        conn.commit()
        conn.close()
        if cancel_requested and cancel_requested():
            return {"text": "Request cancelled.", "chatId": chat_id}
        text_preface, reply, _image_url = run_chat_image_generation(text, uid, chat_id, settings, prefs, model, queue_provider)
        if text_preface:
            reply = f"{text_preface}\n\n{reply}"
        if cancel_requested and cancel_requested():
            return {"text": "Request cancelled.", "chatId": chat_id}
        persist_chat_assistant_result(uid, chat_id, reply, mem_mode, persona_id, workspace_id, b.get("model") or chat["model_override"], text)
        return {"text": reply, "chatId": chat_id}

    sys_msgs = []
    if mem_mode != "off":
        gm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='global'", (uid,)).fetchall()]
        sys_msgs += gm
        if workspace_id:
            wm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=?", (uid, workspace_id))]
            sys_msgs += wm
        if persona_id:
            pm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=?", (uid, persona_id))]
            sys_msgs += pm
        cm = [r[0] for r in conn.execute("SELECT content FROM memories WHERE user_id=? AND tier='chat' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 30", (uid, chat_id)).fetchall()]
        sys_msgs += list(reversed(cm))
    persona_prompt = persona_instruction_block(persona)
    if persona_prompt:
        sys_msgs.append(persona_prompt)
    image_provider = (prefs or {}).get("image_provider", "disabled")
    image_local_backend_override = None
    if image_provider == "local/automatic1111":
        image_provider = "local"
    if image_provider == "local/comfyui":
        image_provider = "local"
        image_local_backend_override = "comfyui"
    image_local_backend = normalize_local_image_backend(image_local_backend_override or (prefs or {}).get("image_local_backend"))
    image_prompt_generation = bool((prefs or {}).get("image_prompt_generation", True))
    if image_prompt_generation:
        sys_msgs.append(model_image_instruction_for_provider(image_provider, image_local_backend))
    sys_msgs.append("The app will ask the user for consent before generating the image.")
    messages = [{"role": "system", "content": "\n".join(sys_msgs)}] if sys_msgs else []
    hist = conn.execute("SELECT role,text FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 20", (chat_id,)).fetchall()
    for r in reversed(hist):
        messages.append({"role": r[0], "content": r[1]})
    messages.append({"role": "user", "content": text})
    conn.commit()
    conn.close()

    if cancel_requested and cancel_requested():
        return {"text": "Request cancelled.", "chatId": chat_id}

    model_image_prompt = ""
    try:
        reply = run_chat_model_generation(model, messages, model_options, prefs, uid, chat_id, queue_provider)
        reply, model_image_prompt = extract_model_image_prompt(reply)
        if image_prompt_generation and model_image_prompt and not image_prompt_is_detailed(model_image_prompt):
            if image_provider == "openai":
                model_image_prompt = adjust_prompt_for_openai_image(model_image_prompt)
            elif image_provider == "local":
                model_image_prompt = adjust_prompt_for_local_sd(model_image_prompt, bool((prefs or {}).get("image_local_allow_nsfw", False)))
    except Exception as e:
        if isinstance(e, MemoryBackpressureError):
            reply = e.user_message
            logger.info(
                "chat backpressure user_id=%s chat_id=%s model=%s detail=%s",
                uid,
                chat_id,
                model,
                json.dumps(e.details, sort_keys=True),
            )
        else:
            logger.exception("model call failed user_id=%s chat_id=%s model=%s", uid, chat_id, model)
            reply = f"Model call failed: {e}"

    if cancel_requested and cancel_requested():
        return {"text": "Request cancelled.", "chatId": chat_id}

    persist_chat_assistant_result(
        uid,
        chat_id,
        reply,
        mem_mode,
        persona_id,
        workspace_id,
        b.get("model") or chat["model_override"],
        text,
        remember_short_facts=True,
    )
    image_offer = {"prompt": model_image_prompt, "message": "Receive image?"} if image_prompt_generation and model_image_prompt else None
    return {"text": reply, "chatId": chat_id, "imageOffer": image_offer}


def chat_queue_shape(uid, payload, chat_id, text):
    settings, prefs = load_user_settings_and_prefs(uid)
    if looks_like_video_request(text):
        return "video", VIDEO_ESTIMATED_VRAM_MB, "bulk", f"video:{(prefs or {}).get('video_model', '')}"
    if looks_like_image_request(text):
        return "image", IMAGE_ESTIMATED_VRAM_MB, "standard", f"image:{(prefs or {}).get('image_provider', 'disabled')}"
    model = (payload or {}).get("model") or ""
    if not model and chat_id:
        conn = db_conn()
        chat = conn.execute("SELECT model_override, persona_id FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
        if chat:
            model = chat["model_override"] or ""
            if not model and chat["persona_id"]:
                persona = conn.execute("SELECT default_model FROM personas WHERE id=?", (chat["persona_id"],)).fetchone()
                model = persona["default_model"] if persona else ""
        conn.close()
    model = model or (settings["global_default_model"] if settings else None) or "llama3"
    return "chat", LLM_ESTIMATED_VRAM_MB, "interactive", f"chat:{model}"


def start_async_chat_job(uid, payload):
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        raise ValueError("text required")
    chat_id = prepare_chat_generation_request(uid, payload, text)
    queue_type, estimated, latency, model_key = chat_queue_shape(uid, payload, chat_id, text)
    async_job_id = create_async_job(uid, chat_id, "chat", progress="Queued")
    submit_async_generation_job(
        async_job_id=async_job_id,
        uid=uid,
        chat_id=chat_id,
        kind="chat",
        job_type=queue_type,
        estimated_vram_mb=estimated,
        latency_class=latency,
        model_key=model_key,
        metadata={"endpoint": "/api/chat"},
        execute=lambda cancel: execute_chat_generation_request(uid, payload, chat_id, queue_provider=False, cancel_requested=cancel),
    )
    return async_job_id, chat_id


def start_async_image_job(uid, payload):
    prompt = str((payload or {}).get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    chat_id = (payload or {}).get("chatId")
    _settings, prefs = load_user_settings_and_prefs(uid)
    image_provider_pref = ((prefs or {}).get("image_provider") or "disabled").strip().lower()
    async_job_id = create_async_job(uid, chat_id, "image", progress="Queued")
    submit_async_generation_job(
        async_job_id=async_job_id,
        uid=uid,
        chat_id=chat_id,
        kind="image",
        job_type="image",
        estimated_vram_mb=IMAGE_ESTIMATED_VRAM_MB,
        latency_class="standard",
        model_key=f"image:{image_provider_pref}",
        metadata={"endpoint": "/api/images/generate"},
        execute=lambda cancel: execute_image_generation_request(uid, payload, cancel_requested=cancel),
    )
    return async_job_id, chat_id


def start_async_video_job(uid, prompt, chat_id, input_reference=None):
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    _settings, prefs = load_user_settings_and_prefs(uid)
    async_job_id = create_async_job(uid, chat_id, "video", progress="Queued")
    submit_async_generation_job(
        async_job_id=async_job_id,
        uid=uid,
        chat_id=chat_id,
        kind="video",
        job_type="video",
        estimated_vram_mb=VIDEO_ESTIMATED_VRAM_MB,
        latency_class="bulk",
        model_key=f"video:{(prefs or {}).get('video_model', '')}",
        metadata={"endpoint": "/api/videos/generate"},
        execute=lambda cancel: execute_video_generation_request(uid, prompt, chat_id, input_reference=input_reference, cancel_requested=cancel),
    )
    return async_job_id, chat_id




class Handler(BaseHTTPRequestHandler):
    server_version = "NiceAssistant/0.1"

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)

    def _set_headers(self, code=200, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")

    def _json(self, data, code=200, cookie=None):
        self._set_headers(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_json(self):
        l = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(l) if l else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except Exception:
            return {}

    def _cookies(self):
        c = SimpleCookie()
        c.load(self.headers.get("Cookie", ""))
        return c

    def _auth_user(self):
        c = self._cookies()
        tok = c.get(SESSION_COOKIE)
        if not tok:
            return None
        conn = db_conn()
        row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE token=?", (tok.value,)).fetchone()
        if not row:
            conn.close()
            return None
        uid = row["user_id"]
        settings = conn.execute("SELECT preferences_json FROM app_settings WHERE user_id=?", (uid,)).fetchone()
        auto_logout_enabled = True
        if settings:
            try:
                prefs = json.loads(settings["preferences_json"] or "{}")
            except (TypeError, ValueError):
                prefs = {}
            auto_logout_enabled = bool(prefs.get("general_auto_logout", True))
        current_ts = now_ts()
        if auto_logout_enabled and row["expires_at"] and row["expires_at"] <= current_ts:
            conn.execute("DELETE FROM sessions WHERE token=?", (tok.value,))
            conn.commit()
            conn.close()
            return None
        if auto_logout_enabled:
            conn.execute("UPDATE sessions SET expires_at=? WHERE token=?", (current_ts + SESSION_TTL_SECONDS, tok.value))
            conn.commit()
        conn.close()
        return uid

    def _require_auth(self):
        uid = self._auth_user()
        if not uid:
            self._json({"error": "unauthorized"}, 401)
            return None
        return uid

    def do_OPTIONS(self):
        self._set_headers(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._json({"ok": True})
        if self.path == "/api/models":
            return self._json({"models": ollama_models()})
        if self.path.startswith("/api/tts/audio/"):
            uid = self._require_auth()
            if not uid:
                return
            aid = self.path.rsplit("/", 1)[-1]
            conn = db_conn(); row = conn.execute("SELECT * FROM audio_files WHERE id=?", (aid,)).fetchone(); conn.close()
            if not row or row["user_id"] != uid:
                return self._json({"error": "not found"}, 404)
            p = Path(row["local_path"])
            if not p.exists():
                return self._json({"error": "missing file"}, 404)
            self._set_headers(200, mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            self.end_headers()
            self.wfile.write(p.read_bytes())
            return
        if self.path.startswith("/api/images/"):
            uid = self._require_auth()
            if not uid:
                return
            iid = self.path.rsplit("/", 1)[-1]
            safe_filename = os.path.basename(iid)
            if not media_file_allowed(uid, "image", safe_filename):
                return self._json({"error": "not found"}, 404)
            image_path = IMAGE_DIR / safe_filename
            if not image_path.exists() or not image_path.is_file():
                return self._json({"error": "not found"}, 404)
            self._set_headers(200, mimetypes.guess_type(str(image_path))[0] or "application/octet-stream")
            self.end_headers()
            self.wfile.write(image_path.read_bytes())
            return
        if self.path.startswith("/api/videos/"):
            uid = self._require_auth()
            if not uid:
                return
            vid = self.path.rsplit("/", 1)[-1]
            safe_filename = os.path.basename(vid)
            if not media_file_allowed(uid, "video", safe_filename):
                return self._json({"error": "not found"}, 404)
            video_path = VIDEO_DIR / safe_filename
            if not video_path.exists() or not video_path.is_file():
                return self._json({"error": "not found"}, 404)
            self._set_headers(200, mimetypes.guess_type(str(video_path))[0] or "application/octet-stream")
            self.end_headers()
            self.wfile.write(video_path.read_bytes())
            return
        if self.path == "/api/workspaces":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); rows = [dict(r) for r in conn.execute("SELECT * FROM workspaces WHERE user_id=?", (uid,)).fetchall()]; conn.close()
            return self._json({"items": rows})
        if self.path == "/api/personas":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn()
            rows = [dict(r) for r in conn.execute("SELECT p.* FROM personas p JOIN workspaces w ON p.workspace_id=w.id WHERE w.user_id=?", (uid,)).fetchall()]
            for row in rows:
                links = [r[0] for r in conn.execute("SELECT workspace_id FROM persona_workspace_links WHERE persona_id=? ORDER BY workspace_id", (row["id"],)).fetchall()]
                row["workspace_ids"] = links or [row["workspace_id"]]
            conn.close()
            return self._json({"items": rows})
        if self.path == "/api/chats":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); rows = [dict(r) for r in conn.execute("SELECT * FROM chats WHERE user_id=? AND COALESCE(hidden_in_ui,0)=0 ORDER BY updated_at DESC", (uid,)).fetchall()]; conn.close()
            return self._json({"items": rows})
        if self.path.startswith("/api/chats/"):
            uid = self._require_auth();
            if not uid: return
            chat_id = self.path.split("/")[3]
            conn = db_conn()
            chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
            msgs = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (chat_id,)).fetchall()]
            conn.close()
            if not chat: return self._json({"error": "not found"}, 404)
            return self._json({"chat": dict(chat), "messages": msgs})
        if self.path.startswith("/api/memory/"):
            uid = self._require_auth();
            if not uid: return
            conn = db_conn()
            if self.path == "/api/memory/all":
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()]
            elif self.path == "/api/memory/global":
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='global'", (uid,)).fetchall()]
            elif self.path.startswith("/api/memory/workspace/"):
                wid = self.path.rsplit("/", 1)[-1]
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=?", (uid, wid)).fetchall()]
            elif self.path.startswith("/api/memory/persona/"):
                pid = self.path.rsplit("/", 1)[-1]
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=?", (uid, pid)).fetchall()]
            elif self.path.startswith("/api/memory/chat/"):
                cid = self.path.rsplit("/", 1)[-1]
                owns = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (cid, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "not found"}, 404)
                rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE user_id=? AND tier='chat' AND tier_ref_id=?", (uid, cid)).fetchall()]
            else:
                conn.close(); return self._json({"error": "unknown tier"}, 400)
            conn.close(); return self._json({"items": rows})
        if self.path == "/api/logs/download":
            uid = self._require_auth();
            if not uid: return
            if not require_admin(uid):
                return self._json({"error": "admin access required"}, 403)
            target = LOG_DIR / "events.log"
            if not target.exists():
                return self._json({"error": "log file unavailable"}, 404)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"nice-assistant-events-{safe_name(uid, 'user')}-{stamp}.txt"
            self._set_headers(200, "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            raw_log = target.read_text(encoding="utf-8", errors="replace")
            self.wfile.write(redact_sensitive_text(raw_log).encode("utf-8"))
            log_interaction("log.download", "user downloaded diagnostic log", user_id=uid)
            return
        if self.path == "/api/settings":
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); row = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            return self._json({"settings": settings_for_response(row)})
        if self.path.startswith("/api/tts/voices"):
            uid = self._require_auth();
            if not uid: return
            conn = db_conn(); settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            if not settings or settings["tts_provider"] != "local":
                return self._json({"voices": []})
            try:
                prefs = json.loads(settings["preferences_json"] or "{}")
            except (TypeError, ValueError):
                prefs = {}
            req_base_url = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("baseUrl", [""])[0]
            base_url = req_base_url.strip() or prefs.get("tts_local_base_url")
            try:
                voices = kokoro_list_voices(base_url)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                return self._json({"error": f"Failed to load local voices: {e}. {detail}"}, 500)
            except Exception as e:
                return self._json({"error": f"Failed to load local voices: {e}"}, 500)
            return self._json({"voices": voices})
        if self.path == "/api/session":
            uid = self._require_auth();
            if not uid: return
            tok = self._cookies().get(SESSION_COOKIE)
            conn = db_conn(); row = conn.execute("SELECT expires_at FROM sessions WHERE token=? AND user_id=?", (tok.value, uid)).fetchone(); conn.close()
            return self._json({"expiresAt": row["expires_at"] if row else None, "ttlSeconds": SESSION_TTL_SECONDS, "now": now_ts()})
        if self.path.startswith("/api/jobs/"):
            uid = self._require_auth()
            if not uid:
                return
            job_id = self.path.rsplit("/", 1)[-1]
            row = get_async_job(uid, job_id)
            if not row:
                return self._json({"error": "not found"}, 404)
            return self._json({"job": async_job_response(row)})
        # static
        rel = self.path if self.path != "/" else "/index.html"
        target = (WEB_DIR / rel.lstrip("/")).resolve()
        if WEB_DIR in target.parents or target == WEB_DIR:
            if target.exists() and target.is_file():
                self._set_headers(200, mimetypes.guess_type(str(target))[0] or "text/plain")
                self.end_headers(); self.wfile.write(target.read_bytes()); return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/users":
            body = self._read_json(); username = body.get("username", "").strip(); password = body.get("password", "")
            if not username or not password: return self._json({"error": "username/password required"}, 400)
            conn = db_conn()
            user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            admin_count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE COALESCE(is_admin,0)=1").fetchone()["c"]
            if admin_count and not ALLOW_PUBLIC_SIGNUP:
                conn.close(); return self._json({"error": "Account creation is disabled after setup."}, 403)
            try:
                uid = secrets.token_hex(8)
                conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES (?,?,?,?,?)", (uid, username, hash_password(password), 1 if user_count == 0 else 0, now_ts()))
                conn.execute("INSERT INTO app_settings(user_id) VALUES (?)", (uid,))
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close(); return self._json({"error": "username exists"}, 409)
            conn.close(); return self._json({"ok": True})
        if self.path == "/api/login":
            body = self._read_json(); username = body.get("username", "").strip(); password = body.get("password", "")
            conn = db_conn(); row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not verify_password(password, row["password_hash"]): conn.close(); return self._json({"error": "invalid credentials"}, 401)
            tok = secrets.token_hex(24)
            created = now_ts()
            expires = created + SESSION_TTL_SECONDS
            conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES (?,?,?,?)", (tok, row["id"], created, expires)); conn.commit(); conn.close()
            return self._json({"ok": True, "userId": row["id"], "expiresAt": expires, "ttlSeconds": SESSION_TTL_SECONDS}, cookie=f"{SESSION_COOKIE}={tok}; Max-Age={60*60*24*30}; Path=/; HttpOnly; SameSite=Lax")
        if self.path == "/api/logout":
            tok = self._cookies().get(SESSION_COOKIE)
            if tok:
                conn = db_conn(); conn.execute("DELETE FROM sessions WHERE token=?", (tok.value,)); conn.commit(); conn.close()
            return self._json({"ok": True}, cookie=f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
        uid = self._require_auth()
        if not uid: return
        if self.path == "/api/logs/client":
            b = self._read_json() or {}
            event_type = str(b.get("type") or CLIENT_EVENT_LOG)
            message = str(b.get("message") or "client event")
            details = b.get("details") if isinstance(b.get("details"), dict) else {}
            log_interaction(event_type, message, user_id=uid, **details)
            return self._json({"ok": True})
        if self.path == "/api/images/generate":
            b = self._read_json() or {}
            prompt = str(b.get("prompt") or "").strip()
            if not prompt:
                return self._json({"error": "prompt required"}, 400)
            if truthy(b.get("async")):
                try:
                    job_id, chat_id = start_async_image_job(uid, b)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "jobId": job_id, "chatId": chat_id, "status": "queued"}, 202)
            _settings, prefs = load_user_settings_and_prefs(uid)
            image_provider_pref = ((prefs or {}).get("image_provider") or "disabled").strip().lower()
            image_job = new_job(
                job_type="image",
                user_id=uid,
                chat_id=b.get("chatId"),
                estimated_vram_mb=IMAGE_ESTIMATED_VRAM_MB,
                latency_class="standard",
                model_key=f"image:{image_provider_pref}",
                metadata={"endpoint": "/api/images/generate"},
                execute=lambda: execute_image_generation_request(uid, b),
            )
            result = JOB_QUEUE.submit(image_job).wait()
            if result.get("ok"):
                return self._json({"ok": True, "text": result.get("text", ""), "imageUrl": result.get("imageUrl")})
            return self._json({"ok": False, "text": result.get("text", "")}, 400)
        if self.path == "/api/videos/generate":
            content_type = self.headers.get("Content-Type", "")
            input_reference = None
            async_requested = False
            if "multipart/form-data" in content_type:
                content_length = int(self.headers.get("Content-Length", "0") or 0)
                raw_body = self.rfile.read(content_length) if content_length else b""
                fields = parse_multipart_form_data(content_type, raw_body)
                prompt = (fields.get("prompt") or {}).get("value", b"").decode("utf-8", errors="replace").strip()
                chat_id = (fields.get("chatId") or {}).get("value", b"").decode("utf-8", errors="replace").strip() or None
                async_requested = truthy((fields.get("async") or {}).get("value", b"").decode("utf-8", errors="replace"))
                file_item = fields.get("input_reference")
                if file_item and file_item.get("value"):
                    input_reference = {
                        "filename": file_item.get("filename") or "reference.png",
                        "content_type": file_item.get("content_type") or "application/octet-stream",
                        "value": file_item.get("value"),
                    }
            else:
                b = self._read_json() or {}
                prompt = str(b.get("prompt") or "").strip()
                chat_id = b.get("chatId")
                async_requested = truthy(b.get("async"))
            if not prompt:
                return self._json({"error": "prompt required"}, 400)
            if async_requested:
                try:
                    job_id, chat_id = start_async_video_job(uid, prompt, chat_id, input_reference=input_reference)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "jobId": job_id, "chatId": chat_id, "status": "queued"}, 202)
            _settings, prefs = load_user_settings_and_prefs(uid)
            video_job = new_job(
                job_type="video",
                user_id=uid,
                chat_id=chat_id,
                estimated_vram_mb=VIDEO_ESTIMATED_VRAM_MB,
                latency_class="bulk",
                model_key=f"video:{(prefs or {}).get('video_model', '')}",
                metadata={"endpoint": "/api/videos/generate"},
                execute=lambda: execute_video_generation_request(uid, prompt, chat_id, input_reference=input_reference),
            )
            result = JOB_QUEUE.submit(video_job).wait()
            if result.get("ok"):
                return self._json({"ok": True, "text": result.get("text", ""), "videoUrl": result.get("videoUrl")})
            return self._json({"ok": False, "text": result.get("text", "")}, 400)
        if self.path == "/api/workspaces":
            b = self._read_json(); wid = secrets.token_hex(8)
            conn = db_conn(); conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES(?,?,?,?)", (wid, uid, b.get("name", "Workspace"), now_ts())); conn.commit(); conn.close()
            return self._json({"id": wid})
        if self.path == "/api/personas":
            b = self._read_json(); pid = secrets.token_hex(8)
            workspace_id = b.get("workspaceId")
            conn = db_conn()
            if not workspace_id:
                conn.close(); return self._json({"error": "workspace required"}, 400)
            if not user_workspace_row(conn, uid, workspace_id):
                conn.close(); return self._json({"error": "workspace not found"}, 404)
            conn.execute("INSERT INTO personas(id,workspace_id,name,avatar_url,system_prompt,personality_details,traits_json,default_model,preferred_voice,preferred_tts_model,preferred_tts_speed,preferred_voice_openai,preferred_tts_model_openai,preferred_tts_speed_openai,preferred_voice_local,preferred_tts_model_local,preferred_tts_speed_local,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                pid, workspace_id, b.get("name", "Persona"), b.get("avatarUrl"), b.get("systemPrompt"), b.get("personalityDetails"), json.dumps(b.get("traits") or {}), b.get("defaultModel"),
                b.get("preferredVoice"), b.get("preferredTtsModel"), b.get("preferredTtsSpeed"),
                b.get("preferred_voice_openai"), b.get("preferred_tts_model_openai"), b.get("preferred_tts_speed_openai"),
                b.get("preferred_voice_local"), b.get("preferred_tts_model_local"), b.get("preferred_tts_speed_local"),
                now_ts(),
            ))
            conn.execute("INSERT OR IGNORE INTO persona_workspace_links(persona_id, workspace_id) VALUES(?,?)", (pid, workspace_id))
            conn.commit(); conn.close()
            return self._json({"id": pid})
        if self.path == "/api/chats":
            b = self._read_json(); cid = secrets.token_hex(8); t=now_ts()
            conn = db_conn()
            try:
                workspace_id, _persona = validate_chat_scope(conn, uid, b.get("workspaceId"), b.get("personaId"))
            except ValueError as exc:
                conn.close(); return self._json({"error": str(exc)}, 404)
            conn.execute("INSERT INTO chats(id,user_id,workspace_id,persona_id,model_override,memory_mode,title,hidden_in_ui,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (cid,uid,workspace_id,b.get("personaId"),b.get("model"),b.get("memoryMode","auto"),b.get("title","New chat"),0,t,t)); conn.commit(); conn.close()
            return self._json({"id": cid})
        if self.path == "/api/chat":
            b = self._read_json() or {}
            text = str(b.get("text") or "").strip()
            if not text:
                return self._json({"error": "text required"}, 400)
            if truthy(b.get("async")):
                try:
                    job_id, chat_id = start_async_chat_job(uid, b)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "jobId": job_id, "chatId": chat_id, "status": "queued"}, 202)
            try:
                chat_id = prepare_chat_generation_request(uid, b, text)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 404)
            result = execute_chat_generation_request(uid, b, chat_id, queue_provider=True)
            return self._json(result)
        if self.path == "/api/settings":
            b = self._read_json()
            conn = db_conn()
            conn.execute("INSERT INTO app_settings(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING", (uid,))
            existing = conn.execute("SELECT openai_api_key FROM app_settings WHERE user_id=?", (uid,)).fetchone()
            existing_key = existing["openai_api_key"] if existing else None
            submitted_key = b.get("openai_api_key")
            if submitted_key is None or submitted_key == "" or is_masked_secret(submitted_key):
                openai_api_key = existing_key
            else:
                openai_api_key = submitted_key
            conn.execute("UPDATE app_settings SET global_default_model=?, default_memory_mode=?, stt_provider=?, tts_provider=?, tts_format=?, openai_api_key=?, onboarding_done=?, preferences_json=? WHERE user_id=?", (
                b.get("global_default_model"), b.get("default_memory_mode","auto"), b.get("stt_provider","disabled"), b.get("tts_provider","disabled"), b.get("tts_format","wav"), openai_api_key, int(bool(b.get("onboarding_done"))), b.get("preferences_json", "{}"), uid
            ))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/memory/"):
            b = self._read_json(); mid=secrets.token_hex(8); tier="global"; ref=None
            if self.path.startswith("/api/memory/workspace/"): tier="workspace"; ref=self.path.rsplit("/",1)[-1]
            elif self.path.startswith("/api/memory/persona/"): tier="persona"; ref=self.path.rsplit("/",1)[-1]
            elif self.path.startswith("/api/memory/chat/"): tier="chat"; ref=self.path.rsplit("/",1)[-1]
            conn = db_conn()
            if tier == "workspace":
                owns = conn.execute("SELECT id FROM workspaces WHERE id=? AND user_id=?", (ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "workspace not found"}, 404)
            elif tier == "persona":
                owns = conn.execute("SELECT p.id FROM personas p WHERE p.id=? AND EXISTS (SELECT 1 FROM persona_workspace_links l JOIN workspaces w ON w.id=l.workspace_id WHERE l.persona_id=p.id AND w.user_id=?)", (ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "persona not found"}, 404)
            elif tier == "chat":
                owns = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "chat not found"}, 404)
            conn.execute("INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)", (mid,uid,tier,ref,b.get("content",""),now_ts())); conn.commit(); conn.close(); return self._json({"id": mid})
        if self.path == "/api/tts":
            b = self._read_json(); text=b.get("text","")
            conn = db_conn(); settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone()
            if not settings or settings["tts_provider"] == "disabled":
                conn.close(); return self._json({"error":"TTS disabled"}, 400)
            fmt = b.get("format") or settings["tts_format"] or "wav"
            persona_id = b.get("personaId")
            persona = conn.execute("SELECT preferred_voice, preferred_tts_model, preferred_tts_speed, preferred_voice_openai, preferred_tts_model_openai, preferred_tts_speed_openai, preferred_voice_local, preferred_tts_model_local, preferred_tts_speed_local FROM personas WHERE id=?", (persona_id,)).fetchone() if persona_id else None
            tts_provider = settings["tts_provider"]
            preferred_voice = (b.get("voice") or ((persona and (persona[f"preferred_voice_{tts_provider}"] if tts_provider in ("openai", "local") else persona["preferred_voice"])) or "")).strip()
            preferred_model = (b.get("model") or ((persona and (persona[f"preferred_tts_model_{tts_provider}"] if tts_provider in ("openai", "local") else persona["preferred_tts_model"])) or "")).strip()
            preferred_speed = (b.get("speed") or ((persona and (persona[f"preferred_tts_speed_{tts_provider}"] if tts_provider in ("openai", "local") else persona["preferred_tts_speed"])) or "")).strip()
            try:
                prefs = json.loads(settings["preferences_json"] or "{}")
            except (TypeError, ValueError):
                prefs = {}
            if not preferred_voice:
                preferred_voice = (prefs.get(f"tts_voice_{tts_provider}") or prefs.get("tts_voice") or ("af_heart" if tts_provider == "local" else "alloy")).strip()
            if not preferred_model:
                preferred_model = (prefs.get(f"tts_model_{tts_provider}") or prefs.get("tts_model") or ("kokoro" if tts_provider == "local" else "gpt-4o-mini-tts")).strip()
            if not preferred_speed:
                preferred_speed = str(prefs.get(f"tts_speed_{tts_provider}") or prefs.get("tts_speed") or "1")
            local_tts_base_url = prefs.get("tts_local_base_url")
            conn.close()
            out_id = secrets.token_hex(8)
            out_path = AUDIO_DIR / f"{out_id}.{fmt}"
            if settings["tts_provider"] == "openai":
                key = settings["openai_api_key"]
                if not key: return self._json({"error":"OPENAI API key missing"}, 400)
                try:
                    audio = openai_speech(text, preferred_voice, fmt, key, preferred_model, preferred_speed)
                    out_path.write_bytes(audio)
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", errors="replace")
                    return self._json({"error": f"TTS failed: {e}. {detail}"}, 500)
                except Exception as e:
                    return self._json({"error": f"TTS failed: {e}"}, 500)
            elif settings["tts_provider"] == "local":
                try:
                    audio = kokoro_speech(text, preferred_voice, fmt, local_tts_base_url, preferred_model, preferred_speed)
                    out_path.write_bytes(audio)
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", errors="replace")
                    return self._json({"error": f"Local TTS failed: {e}. {detail}"}, 500)
                except Exception as e:
                    return self._json({"error": f"Local TTS failed: {e}"}, 500)
            else:
                return self._json({"error":"Unknown TTS provider"}, 400)
            conn = db_conn(); conn.execute("INSERT INTO audio_files(id,user_id,persona_id,chat_id,format,local_path,created_at) VALUES(?,?,?,?,?,?,?)", (out_id, uid, b.get("personaId"), b.get("chatId"), fmt, str(out_path), now_ts())); conn.commit(); conn.close()
            rotate_audio_cache()
            return self._json({"audioUrl": f"/api/tts/audio/{out_id}", "format": fmt})
        if self.path == "/api/stt":
            conn = db_conn(); settings = conn.execute("SELECT * FROM app_settings WHERE user_id=?", (uid,)).fetchone(); conn.close()
            if not settings or settings["stt_provider"] == "disabled":
                log_interaction("stt.error", "stt requested while disabled", user_id=uid)
                return self._json({"error":"STT disabled"}, 400)
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            content_type = self.headers.get("Content-Type", "")
            raw_body = self.rfile.read(content_length) if content_length else b""
            fields = parse_multipart_form_data(content_type, raw_body)
            fitem = fields.get("file")
            if not fitem or not fitem.get("value"):
                log_interaction("stt.error", "missing audio file in request", user_id=uid)
                return self._json({"error": "file required"}, 400)
            ext = ".webm"
            incoming = str((fitem.get("filename") or "").lower())
            if incoming.endswith(".mp4") or incoming.endswith(".m4a"):
                ext = ".mp4"
            elif incoming.endswith(".ogg"):
                ext = ".ogg"
            raw = DATA_DIR / f"upload_{secrets.token_hex(6)}{ext}"
            wav = DATA_DIR / f"upload_{secrets.token_hex(6)}.wav"
            try:
                with open(raw, "wb") as f:
                    f.write(fitem["value"])
                ffmpeg = subprocess.run(["ffmpeg", "-y", "-i", str(raw), str(wav)], check=False, capture_output=True)
                if ffmpeg.returncode != 0 or not wav.exists():
                    detail = ffmpeg.stderr.decode("utf-8", errors="replace")[:500]
                    log_interaction("stt.error", "ffmpeg conversion failed", user_id=uid, return_code=ffmpeg.returncode, detail=detail)
                    return self._json({"error": "Audio conversion failed. Please try again."}, 500)
                if settings["stt_provider"] == "openai":
                    key = settings["openai_api_key"]
                    if not key:
                        log_interaction("stt.error", "openai key missing for stt", user_id=uid)
                        return self._json({"error":"OPENAI API key missing"}, 400)
                    prefs = parse_preferences_json(settings["preferences_json"])
                    lang = prefs.get("stt_language") or "auto"
                    try:
                        data = openai_stt(str(wav), key, lang)
                        if setting_bool(settings, "stt_store_recordings", False):
                            stored_raw = STT_RECORDINGS_DIR / f"{uid}_{int(time.time())}_{safe_name(raw.name, 'audio'+ext)}"
                            shutil.copy2(raw, stored_raw)
                        log_interaction("stt.success", "speech transcription complete", user_id=uid, language=data.get("language"), chars=len(data.get("text", "")))
                        return self._json({"text": data.get("text", ""), "language": data.get("language")})
                    except Exception as e:
                        log_interaction("stt.error", "openai stt failure", user_id=uid, error=str(e)[:500])
                        return self._json({"error": f"STT failed: {e}"}, 500)
                return self._json({"error":"Local provider not implemented yet"}, 400)
            finally:
                if raw.exists():
                    raw.unlink(missing_ok=True)
                if wav.exists():
                    wav.unlink(missing_ok=True)
        if self.path == "/api/tts/stream":
            return self._json({"todo": "Streaming TTS endpoint foundation placeholder"}, 501)
        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        uid = self._require_auth();
        if not uid: return
        if self.path.startswith("/api/chats/"):
            chat_id = self.path.rsplit("/", 1)[-1]; b = self._read_json()
            conn = db_conn()
            chat = conn.execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, uid)).fetchone()
            if not chat:
                conn.close(); return self._json({"error": "not found"}, 404)
            conn.execute("UPDATE chats SET title=?, model_override=?, memory_mode=?, persona_id=?, hidden_in_ui=?, updated_at=? WHERE id=? AND user_id=?", (
                b.get("title", chat["title"]),
                b.get("model_override", chat["model_override"]),
                b.get("memory_mode", chat["memory_mode"]),
                b.get("persona_id", chat["persona_id"]),
                int(bool(b.get("hidden_in_ui", chat["hidden_in_ui"]))),
                now_ts(),
                chat_id,
                uid,
            ))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/workspaces/"):
            wid = self.path.rsplit("/", 1)[-1]; b = self._read_json()
            new_name = (b.get("name") or "").strip()
            if not new_name: return self._json({"error": "name required"}, 400)
            conn = db_conn()
            row = conn.execute("SELECT id FROM workspaces WHERE id=? AND user_id=?", (wid, uid)).fetchone()
            if not row:
                conn.close(); return self._json({"error": "not found"}, 404)
            conn.execute("UPDATE workspaces SET name=? WHERE id=? AND user_id=?", (new_name, wid, uid))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/personas/"):
            pid = self.path.rsplit("/", 1)[-1]; b = self._read_json()
            conn = db_conn()
            row = conn.execute("SELECT p.* FROM personas p WHERE p.id=? AND EXISTS (SELECT 1 FROM persona_workspace_links l JOIN workspaces w ON w.id=l.workspace_id WHERE l.persona_id=p.id AND w.user_id=?)", (pid, uid)).fetchone()
            if not row:
                conn.close(); return self._json({"error": "not found"}, 404)
            workspace_ids = b.get("workspace_ids")
            if workspace_ids is not None:
                workspace_ids = [wid for wid in workspace_ids if wid]
                if not workspace_ids:
                    conn.close(); return self._json({"error": "workspace_ids must include at least one workspace"}, 400)
                for wid in workspace_ids:
                    owns_workspace = conn.execute("SELECT id FROM workspaces WHERE id=? AND user_id=?", (wid, uid)).fetchone()
                    if not owns_workspace:
                        conn.close(); return self._json({"error": "workspace not found"}, 404)
            else:
                workspace_ids = [r[0] for r in conn.execute("SELECT workspace_id FROM persona_workspace_links WHERE persona_id=?", (pid,)).fetchall()] or [row["workspace_id"]]
                if b.get("workspace_id") and b.get("workspace_id") not in workspace_ids:
                    workspace_ids.append(b.get("workspace_id"))
            workspace_id = b.get("workspace_id", row["workspace_id"])
            if workspace_id not in workspace_ids:
                workspace_ids.insert(0, workspace_id)
            conn.execute("UPDATE personas SET name=?, system_prompt=?, default_model=?, workspace_id=? WHERE id=?", (
                b.get("name", row["name"]),
                b.get("system_prompt", row["system_prompt"]),
                b.get("default_model", row["default_model"]),
                workspace_id,
                pid,
            ))
            if "avatar_url" in b or "personality_details" in b or "traits" in b or "preferred_voice" in b or "preferred_tts_model" in b or "preferred_tts_speed" in b or "preferred_voice_openai" in b or "preferred_tts_model_openai" in b or "preferred_tts_speed_openai" in b or "preferred_voice_local" in b or "preferred_tts_model_local" in b or "preferred_tts_speed_local" in b:
                conn.execute("UPDATE personas SET avatar_url=?, personality_details=?, traits_json=?, preferred_voice=?, preferred_tts_model=?, preferred_tts_speed=?, preferred_voice_openai=?, preferred_tts_model_openai=?, preferred_tts_speed_openai=?, preferred_voice_local=?, preferred_tts_model_local=?, preferred_tts_speed_local=? WHERE id=?", (
                    b.get("avatar_url", row["avatar_url"]),
                    b.get("personality_details", row["personality_details"]),
                    json.dumps(b.get("traits", json.loads(row["traits_json"] or "{}"))),
                    b.get("preferred_voice", row["preferred_voice"]),
                    b.get("preferred_tts_model", row["preferred_tts_model"]),
                    b.get("preferred_tts_speed", row["preferred_tts_speed"]),
                    b.get("preferred_voice_openai", row["preferred_voice_openai"]),
                    b.get("preferred_tts_model_openai", row["preferred_tts_model_openai"]),
                    b.get("preferred_tts_speed_openai", row["preferred_tts_speed_openai"]),
                    b.get("preferred_voice_local", row["preferred_voice_local"]),
                    b.get("preferred_tts_model_local", row["preferred_tts_model_local"]),
                    b.get("preferred_tts_speed_local", row["preferred_tts_speed_local"]),
                    pid,
                ))
            conn.execute("DELETE FROM persona_workspace_links WHERE persona_id=?", (pid,))
            for wid in workspace_ids:
                conn.execute("INSERT OR IGNORE INTO persona_workspace_links(persona_id, workspace_id) VALUES(?,?)", (pid, wid))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/memory/"):
            mid = self.path.rsplit("/", 1)[-1]; b = self._read_json()
            new_tier = b.get("tier")
            new_ref = b.get("tier_ref_id")
            conn = db_conn()
            row = conn.execute("SELECT * FROM memories WHERE id=? AND user_id=?", (mid, uid)).fetchone()
            if not row:
                conn.close(); return self._json({"error": "not found"}, 404)
            if new_tier in ["workspace", "persona", "chat"] and not new_ref:
                conn.close(); return self._json({"error": "tier_ref_id required"}, 400)
            if new_tier == "workspace":
                owns = conn.execute("SELECT id FROM workspaces WHERE id=? AND user_id=?", (new_ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "workspace not found"}, 404)
            elif new_tier == "persona":
                owns = conn.execute("SELECT p.id FROM personas p WHERE p.id=? AND EXISTS (SELECT 1 FROM persona_workspace_links l JOIN workspaces w ON w.id=l.workspace_id WHERE l.persona_id=p.id AND w.user_id=?)", (new_ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "persona not found"}, 404)
            elif new_tier == "chat":
                owns = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (new_ref, uid)).fetchone()
                if not owns:
                    conn.close(); return self._json({"error": "chat not found"}, 404)
            conn.execute("UPDATE memories SET content=?, tier=?, tier_ref_id=? WHERE id=? AND user_id=?", (
                b.get("content", row["content"]),
                new_tier or row["tier"],
                new_ref if new_tier else row["tier_ref_id"],
                mid,
                uid,
            ))
            conn.commit(); conn.close(); return self._json({"ok": True})
        return self._json({"error":"not found"},404)

    def do_DELETE(self):
        uid = self._require_auth();
        if not uid: return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            row = cancel_async_job(uid, job_id)
            if not row:
                return self._json({"error": "not found"}, 404)
            return self._json({"job": async_job_response(row)})
        if self.path.startswith("/api/memory/"):
            mid=self.path.rsplit("/",1)[-1]
            conn=db_conn(); conn.execute("DELETE FROM memories WHERE id=? AND user_id=?", (mid,uid)); conn.commit(); conn.close(); return self._json({"ok":True})
        if self.path.startswith("/api/chats/"):
            chat_id = self.path.rsplit("/", 1)[-1]
            conn = db_conn(); conn.execute("UPDATE chats SET hidden_in_ui=1, updated_at=? WHERE id=? AND user_id=?", (now_ts(), chat_id, uid)); conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/personas/"):
            pid = self.path.rsplit("/", 1)[-1]
            conn = db_conn()
            owns = conn.execute("SELECT p.id FROM personas p WHERE p.id=? AND EXISTS (SELECT 1 FROM persona_workspace_links l JOIN workspaces w ON w.id=l.workspace_id WHERE l.persona_id=p.id AND w.user_id=?)", (pid, uid)).fetchone()
            if not owns:
                conn.close(); return self._json({"error": "not found"}, 404)
            conn.execute("UPDATE chats SET persona_id=NULL WHERE user_id=? AND persona_id=?", (uid, pid))
            conn.execute("DELETE FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=?", (uid, pid))
            conn.execute("DELETE FROM persona_workspace_links WHERE persona_id=?", (pid,))
            conn.execute("DELETE FROM personas WHERE id=?", (pid,))
            conn.commit(); conn.close(); return self._json({"ok": True})
        if self.path.startswith("/api/workspaces/"):
            wid = self.path.rsplit("/", 1)[-1]
            conn = db_conn()
            owns = conn.execute("SELECT id FROM workspaces WHERE id=? AND user_id=?", (wid, uid)).fetchone()
            if not owns:
                conn.close(); return self._json({"error": "not found"}, 404)
            persona_count = conn.execute("SELECT COUNT(*) AS c FROM persona_workspace_links WHERE workspace_id=?", (wid,)).fetchone()["c"]
            chat_count = conn.execute("SELECT COUNT(*) AS c FROM chats WHERE user_id=? AND workspace_id=?", (uid, wid)).fetchone()["c"]
            if persona_count or chat_count:
                conn.close(); return self._json({"error": "workspace not empty; remove personas/chats first"}, 400)
            conn.execute("DELETE FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=?", (uid, wid))
            conn.execute("DELETE FROM workspaces WHERE id=? AND user_id=?", (wid, uid))
            conn.commit(); conn.close(); return self._json({"ok": True})
        return self._json({"error":"not found"},404)


def main():
    ensure_dirs(); setup_file_logger(); init_db(); rotate_logs(); backup_db_if_needed()
    MEMORY_GUARD.start()
    server = GracefulThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    shutdown_requested = threading.Event()

    def shutdown_handler(signum, _frame):
        if shutdown_requested.is_set():
            logger.info("shutdown already in progress signal=%s", signum)
            return
        shutdown_requested.set()
        logger.info("shutdown signal received signal=%s active_threads=%d", signum, threading.active_count())
        threading.Thread(target=server.shutdown, name="server-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    logger.info("Nice Assistant listening on %s", PORT)
    started = time.monotonic()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        MEMORY_GUARD.stop()
        server.server_close()
        logger.info("http server closed uptime_seconds=%.2f", time.monotonic() - started)


if __name__ == "__main__":
    main()
