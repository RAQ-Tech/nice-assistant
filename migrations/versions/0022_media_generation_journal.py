"""Add the per-generation journal and its append-only stages.

The journal is additive: existing plans, attempts, and media are untouched, and
generations that completed before this migration simply have no journal.
"""

from __future__ import annotations

from alembic import op


revision = "0022_media_generation_journal"
down_revision = "0021_persona_lorebooks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE media_generation_journals (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT REFERENCES chats(id) ON DELETE SET NULL,
            persona_id TEXT,
            media_plan_id TEXT REFERENCES media_execution_plans(id) ON DELETE SET NULL,
            capability_request_id TEXT,
            media_id TEXT REFERENCES media_files(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('image','video')),
            origin TEXT NOT NULL CHECK (origin IN ('conversation','direct','edit','library')),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','completed','failed','cancelled')),
            error_code TEXT,
            error_message TEXT,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            duration_ms INTEGER
        )
        """
    )
    op.execute("CREATE INDEX idx_media_journal_owner_started ON media_generation_journals(user_id, started_at)")
    op.execute("CREATE INDEX idx_media_journal_media ON media_generation_journals(media_id)")
    op.execute("CREATE INDEX idx_media_journal_plan ON media_generation_journals(media_plan_id)")
    op.execute(
        """
        CREATE TABLE media_generation_journal_stages (
            id TEXT PRIMARY KEY,
            journal_id TEXT NOT NULL REFERENCES media_generation_journals(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','skipped','failed')),
            summary TEXT NOT NULL DEFAULT '',
            detail_json TEXT NOT NULL DEFAULT '{}',
            started_at INTEGER NOT NULL,
            duration_ms INTEGER,
            UNIQUE (journal_id, sequence)
        )
        """
    )
    op.execute("CREATE INDEX idx_media_journal_stage_journal ON media_generation_journal_stages(journal_id, sequence)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
