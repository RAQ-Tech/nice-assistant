"""Bring pre-Alembic databases to the typed-settings schema."""

import json
import time

from alembic import op
import sqlalchemy as sa


revision = "0002_adopt_existing_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _columns(bind, table):
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _add_missing_columns(bind):
    required = {
        "users": {"is_admin": "INTEGER DEFAULT 0"},
        "sessions": {"expires_at": "INTEGER"},
        "chats": {"hidden_in_ui": "INTEGER DEFAULT 0"},
        "app_settings": {
            "preferences_json": "TEXT DEFAULT '{}'",
            "openai_api_key_encrypted": "TEXT",
        },
        "personas": {
            "personality_details": "TEXT",
            "traits_json": "TEXT DEFAULT '{}'",
            "preferred_tts_model": "TEXT",
            "preferred_tts_speed": "TEXT",
            "preferred_voice_openai": "TEXT",
            "preferred_tts_model_openai": "TEXT",
            "preferred_tts_speed_openai": "TEXT",
            "preferred_voice_local": "TEXT",
            "preferred_tts_model_local": "TEXT",
            "preferred_tts_speed_local": "TEXT",
        },
        "async_jobs": {
            "cancel_requested": "INTEGER DEFAULT 0",
            "started_at": "INTEGER",
            "updated_at": "INTEGER",
            "completed_at": "INTEGER",
            "progress": "TEXT",
            "result_json": "TEXT",
            "error": "TEXT",
        },
    }
    tables = set(sa.inspect(bind).get_table_names())
    for table, columns in required.items():
        if table not in tables:
            continue
        existing = _columns(bind, table)
        for name, definition in columns.items():
            if name not in existing:
                bind.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_preferences(bind):
    rows = bind.exec_driver_sql("SELECT user_id, preferences_json FROM app_settings").fetchall()
    stamp = int(time.time())
    for user_id, raw in rows:
        try:
            values = json.loads(raw or "{}")
        except (TypeError, ValueError):
            values = {}
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, bool):
                kind = "bool"
            elif isinstance(value, int):
                kind = "int"
            elif isinstance(value, float):
                kind = "float"
            elif value is None:
                kind = "null"
            elif isinstance(value, str):
                kind = "str"
            else:
                kind = "json"
            bind.exec_driver_sql(
                "INSERT OR IGNORE INTO setting_values(user_id,key,value_type,value_json,updated_at) VALUES(?,?,?,?,?)",
                (user_id, str(key)[:120], kind, json.dumps(value, separators=(",", ":")), stamp),
            )


def upgrade():
    bind = op.get_bind()
    _add_missing_columns(bind)
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS setting_values (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key VARCHAR(120) NOT NULL, value_type VARCHAR(16) NOT NULL, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL, CONSTRAINT uq_setting_values_user_key UNIQUE(user_id,key), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
    )
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_setting_values_user ON setting_values(user_id)")
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS persona_workspace_links (persona_id TEXT NOT NULL, workspace_id TEXT NOT NULL, PRIMARY KEY(persona_id,workspace_id), FOREIGN KEY(persona_id) REFERENCES personas(id) ON DELETE CASCADE, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE)"
    )
    bind.exec_driver_sql(
        "INSERT OR IGNORE INTO persona_workspace_links(persona_id,workspace_id) SELECT id,workspace_id FROM personas WHERE workspace_id IS NOT NULL"
    )
    _migrate_preferences(bind)


def downgrade():
    pass
