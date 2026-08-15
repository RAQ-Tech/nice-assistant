"""Allow the scene proposal task-model role.

The role list is a CHECK constraint, and SQLite cannot alter one in place, so
the table is rebuilt. Existing profiles are copied verbatim; no row's
configuration changes, and the new role is seeded lazily like every other.
"""

from __future__ import annotations

from alembic import op


revision = "0029_scene_proposal_role"
down_revision = "0028_persona_scene_backlog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE task_model_profiles RENAME TO task_model_profiles_old")
    op.execute(
        """
        CREATE TABLE task_model_profiles (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (
                role IN (
                    'title_generation',
                    'conversation_summary',
                    'memory_extraction',
                    'capability_planning',
                    'scene_proposal'
                )
            ),
            provider TEXT NOT NULL,
            model TEXT,
            fallback_provider TEXT,
            fallback_model TEXT,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
            max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens BETWEEN 128 AND 262144),
            max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens BETWEEN 16 AND 8192),
            timeout_seconds REAL NOT NULL CHECK (timeout_seconds BETWEEN 1 AND 600),
            temperature REAL NOT NULL CHECK (temperature BETWEEN 0 AND 2),
            fallback_policy TEXT NOT NULL CHECK (fallback_policy IN ('deterministic','skip','fail')),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE (user_id, role)
        )
        """
    )
    op.execute(
        """
        INSERT INTO task_model_profiles (
            id, user_id, role, provider, model, fallback_provider, fallback_model, enabled,
            max_input_tokens, max_output_tokens, timeout_seconds, temperature, fallback_policy,
            created_at, updated_at
        )
        SELECT
            id, user_id, role, provider, model, fallback_provider, fallback_model, enabled,
            max_input_tokens, max_output_tokens, timeout_seconds, temperature, fallback_policy,
            created_at, updated_at
        FROM task_model_profiles_old
        """
    )
    op.execute("DROP TABLE task_model_profiles_old")
    op.execute("CREATE INDEX idx_task_model_profiles_user ON task_model_profiles(user_id, role)")

    # The run ledger constrains the same vocabulary, so it is rebuilt too.
    op.execute("ALTER TABLE task_model_runs RENAME TO task_model_runs_old")
    op.execute(
        """
        CREATE TABLE task_model_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (
                role IN (
                    'title_generation',
                    'conversation_summary',
                    'memory_extraction',
                    'capability_planning',
                    'scene_proposal'
                )
            ),
            chat_id TEXT REFERENCES chats(id) ON DELETE SET NULL,
            turn_id TEXT REFERENCES conversation_turns(id) ON DELETE SET NULL,
            requested_provider TEXT,
            requested_model TEXT,
            executed_provider TEXT,
            executed_model TEXT,
            status TEXT NOT NULL CHECK (status IN ('running','completed','fallback','failed')),
            fallback_used INTEGER NOT NULL DEFAULT 0 CHECK (fallback_used IN (0,1)),
            error_code TEXT,
            error_message TEXT,
            attempts_json TEXT NOT NULL DEFAULT '[]',
            input_tokens_estimated INTEGER,
            output_tokens_estimated INTEGER,
            latency_ms INTEGER,
            started_at INTEGER NOT NULL,
            completed_at INTEGER
        )
        """
    )
    op.execute(
        """
        INSERT INTO task_model_runs (
            id, user_id, role, chat_id, turn_id, requested_provider, requested_model,
            executed_provider, executed_model, status, fallback_used, error_code, error_message,
            attempts_json, input_tokens_estimated, output_tokens_estimated, latency_ms,
            started_at, completed_at
        )
        SELECT
            id, user_id, role, chat_id, turn_id, requested_provider, requested_model,
            executed_provider, executed_model, status, fallback_used, error_code, error_message,
            attempts_json, input_tokens_estimated, output_tokens_estimated, latency_ms,
            started_at, completed_at
        FROM task_model_runs_old
        """
    )
    op.execute("DROP TABLE task_model_runs_old")
    op.execute("CREATE INDEX idx_task_model_runs_user_started ON task_model_runs(user_id, started_at)")
    op.execute("CREATE INDEX idx_task_model_runs_user_role ON task_model_runs(user_id, role, started_at)")
    op.execute("CREATE INDEX idx_task_model_runs_turn ON task_model_runs(turn_id)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
