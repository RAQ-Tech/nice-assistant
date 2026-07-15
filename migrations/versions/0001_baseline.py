"""Create or adopt the legacy-compatible baseline schema."""

from alembic import op


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS personas (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL, avatar_url TEXT, system_prompt TEXT, personality_details TEXT, traits_json TEXT DEFAULT '{}', default_model TEXT, preferred_voice TEXT, preferred_tts_model TEXT, preferred_tts_speed TEXT, preferred_voice_openai TEXT, preferred_tts_model_openai TEXT, preferred_tts_speed_openai TEXT, preferred_voice_local TEXT, preferred_tts_model_local TEXT, preferred_tts_speed_local TEXT, created_at INTEGER NOT NULL, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS persona_workspace_links (persona_id TEXT NOT NULL, workspace_id TEXT NOT NULL, PRIMARY KEY(persona_id, workspace_id), FOREIGN KEY(persona_id) REFERENCES personas(id) ON DELETE CASCADE, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT, persona_id TEXT, model_override TEXT, memory_mode TEXT DEFAULT 'auto', title TEXT, hidden_in_ui INTEGER DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL, FOREIGN KEY(persona_id) REFERENCES personas(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, tier TEXT NOT NULL, tier_ref_id TEXT, content TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS app_settings (user_id TEXT PRIMARY KEY, global_default_model TEXT, default_memory_mode TEXT DEFAULT 'auto', stt_provider TEXT DEFAULT 'disabled', tts_provider TEXT DEFAULT 'disabled', tts_format TEXT DEFAULT 'wav', openai_api_key TEXT, openai_api_key_encrypted TEXT, onboarding_done INTEGER DEFAULT 0, preferences_json TEXT DEFAULT '{}', FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS setting_values (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key VARCHAR(120) NOT NULL, value_type VARCHAR(16) NOT NULL, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL, CONSTRAINT uq_setting_values_user_key UNIQUE(user_id,key), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS audio_files (id TEXT PRIMARY KEY, user_id TEXT, persona_id TEXT, chat_id TEXT, format TEXT NOT NULL, local_path TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(persona_id) REFERENCES personas(id) ON DELETE SET NULL, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS media_files (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT, kind TEXT NOT NULL, filename TEXT NOT NULL, local_path TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS async_jobs (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL, cancel_requested INTEGER DEFAULT 0, created_at INTEGER NOT NULL, started_at INTEGER, updated_at INTEGER NOT NULL, completed_at INTEGER, progress TEXT, result_json TEXT, error TEXT, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS idx_media_files_kind_filename ON media_files(kind, filename);
CREATE INDEX IF NOT EXISTS idx_async_jobs_user_status ON async_jobs(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_setting_values_user ON setting_values(user_id);
"""


def upgrade():
    bind = op.get_bind()
    for statement in SCHEMA.split(";"):
        if statement.strip():
            bind.exec_driver_sql(statement)


def downgrade():
    pass
