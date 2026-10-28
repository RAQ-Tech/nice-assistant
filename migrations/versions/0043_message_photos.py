"""Photos sent with a message, for a persona whose model can see.

Kept in the database beside the message they belong to, so deleting the
message deletes the photo and every backup that has the message has it too.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0043_message_photos"
down_revision = "0042_streamable_default_speech_format"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_photos",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Text(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("idx_message_photos_message", "message_photos", ["message_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_message_photos_message", table_name="message_photos")
    op.drop_table("message_photos")
