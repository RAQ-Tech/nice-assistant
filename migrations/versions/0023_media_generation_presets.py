"""Add generation presets: the tested recipe planning selects.

Additive only. Existing catalog resources, plans, and media are untouched.
Owners are backfilled lazily on first preset use, the same way the media catalog
already imports legacy provider settings, so no account is rewritten here.
"""

from __future__ import annotations

from alembic import op


revision = "0023_media_generation_presets"
down_revision = "0022_media_generation_journal"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE media_generation_presets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('image','video')),
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
            routing_card TEXT NOT NULL DEFAULT '',
            operations_json TEXT NOT NULL DEFAULT '[]',
            domains_json TEXT NOT NULL DEFAULT '[]',
            content_tags_json TEXT NOT NULL DEFAULT '[]',
            features_json TEXT NOT NULL DEFAULT '[]',
            definition_json TEXT NOT NULL DEFAULT '{}',
            estimated_vram_mb INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            UNIQUE (user_id, name)
        )
        """
    )
    op.execute("CREATE INDEX idx_media_preset_owner_kind_enabled ON media_generation_presets(user_id, kind, enabled)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
