"""Add photo sets, and link retained pictures to the set that produced them.

A set is a shared scene plus what changes between frames. Recorded separately
from the scene backlog because a backlog entry is one proposed picture, and a
set is one idea that becomes several - conflating them would make "six frames
planned" and "six pictures waiting" indistinguishable.

Additive and empty.
"""

from __future__ import annotations

from alembic import op


revision = "0032_photo_sets"
down_revision = "0031_chat_binding_repair"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE persona_photo_sets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
            scene_json TEXT NOT NULL DEFAULT '{}',
            variations_json TEXT NOT NULL DEFAULT '[]',
            state TEXT NOT NULL DEFAULT 'planned'
                CHECK (state IN ('planned','generating','done','partial','retired')),
            base_seed INTEGER NOT NULL DEFAULT 0,
            frame_count INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_photo_sets_owner_persona_state ON persona_photo_sets(user_id, persona_id, state)")
    # A retained picture remembers which set and which frame it is, so several
    # frames of one set can be served together later.
    op.execute(
        "ALTER TABLE persona_image_library ADD COLUMN photo_set_id TEXT "
        "REFERENCES persona_photo_sets(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE persona_image_library ADD COLUMN frame_index INTEGER")
    op.execute("CREATE INDEX idx_library_photo_set ON persona_image_library(photo_set_id, frame_index)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
