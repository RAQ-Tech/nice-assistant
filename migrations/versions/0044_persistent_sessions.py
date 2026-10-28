"""Sessions somebody chose to keep on a device.

A kept session is renewed by use and never ended for being idle (ADR 0047).
Every session that exists already keeps the behavior it was created with.
"""

from __future__ import annotations

from alembic import op


revision = "0044_persistent_sessions"
down_revision = "0043_message_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("persistent")
