from pathlib import Path
import os
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


def initialize_database(path, session_ttl_seconds, secret_store=None):
    secret_store = secret_store or SECRET_STORE
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    upgrade_database(path)
    conn = connect_sqlite(path)
    stamp = int(time.time())
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
