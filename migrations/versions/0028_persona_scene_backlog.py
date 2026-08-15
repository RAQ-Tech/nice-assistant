"""Add the per-persona scene backlog.

Additive and empty. Nothing generates from it yet; this records what has been
proposed and where the idea came from, so a proposal can be judged rather than
accepted blindly.
"""

from __future__ import annotations

from alembic import op


revision = "0028_persona_scene_backlog"
down_revision = "0027_persona_preset_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE persona_scene_backlog (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
            scene_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'proposed'
                CHECK (state IN ('proposed','approved','generating','done','retired')),
            source TEXT NOT NULL DEFAULT 'operator'
                CHECK (source IN ('operator','persona_card','lorebook','conversation')),
            source_detail TEXT NOT NULL DEFAULT '',
            media_id TEXT REFERENCES media_files(id) ON DELETE SET NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_scene_backlog_owner_persona_state ON persona_scene_backlog(user_id, persona_id, state)"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
