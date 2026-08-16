"""Let one attachment carry several frames of the same photo set.

A set is only worth generating if it can arrive as a set. The attachment keeps
its own `media_id` as the frame shown first, so every existing reader is
unaffected, and the additional frames are rows beside it rather than a second
meaning for a column that already has one.

Additive and empty.
"""

from __future__ import annotations

from alembic import op


revision = "0033_attachment_frames"
down_revision = "0032_photo_sets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE chat_attachment_frames (
            id TEXT PRIMARY KEY,
            attachment_id TEXT NOT NULL REFERENCES chat_attachments(id) ON DELETE CASCADE,
            media_id TEXT NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
            frame_index INTEGER,
            position INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_attachment_frames_media ON chat_attachment_frames(attachment_id, media_id)")
    op.execute("CREATE INDEX idx_attachment_frames_attachment ON chat_attachment_frames(attachment_id, position)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
