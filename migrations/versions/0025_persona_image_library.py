"""Retain generated pictures with the scene that produced them.

Additive. Nothing existing is retained retroactively: a picture generated before
this has no scene recorded, and inventing one would make the library claim
knowledge it does not have.
"""

from __future__ import annotations

from alembic import op


revision = "0025_persona_image_library"
down_revision = "0024_identity_spec"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE persona_image_library (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            persona_id TEXT REFERENCES personas(id) ON DELETE CASCADE,
            media_id TEXT NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
            scene_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','served','retired')),
            origin_chat_id TEXT,
            served_count INTEGER NOT NULL DEFAULT 0,
            last_served_chat_id TEXT,
            created_at INTEGER NOT NULL,
            last_served_at INTEGER,
            UNIQUE (user_id, media_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_library_owner_persona_state ON persona_image_library(user_id, persona_id, state)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
