"""Add persona lorebook entries."""

from __future__ import annotations

from alembic import op


revision = "0021_persona_lorebooks"
down_revision = "0020_persona_example_dialogue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE persona_lore_entries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            keys_json TEXT NOT NULL,
            secondary_keys_json TEXT NOT NULL DEFAULT '[]',
            content TEXT NOT NULL,
            always_on INTEGER NOT NULL DEFAULT 0 CHECK (always_on IN (0,1)),
            case_sensitive INTEGER NOT NULL DEFAULT 0 CHECK (case_sensitive IN (0,1)),
            priority INTEGER NOT NULL DEFAULT 50 CHECK (priority >= 0 AND priority <= 100),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
            token_estimate INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_persona_lore_owner_persona_enabled ON persona_lore_entries(user_id, persona_id, enabled)"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
