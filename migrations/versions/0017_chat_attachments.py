"""Add durable chat attachments and human-oriented image preferences."""

from __future__ import annotations

import json
import time

from alembic import op
import sqlalchemy as sa


revision = "0017_chat_attachments"
down_revision = "0016_identity_fallback"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite batch recreation would cascade-delete durable rows that reference
    # capability_requests. Extend the table in place and retain the legacy
    # constrained column for older consumers.
    op.execute(
        "ALTER TABLE capability_requests ADD COLUMN permission_mode_effective "
        "TEXT NOT NULL DEFAULT 'confirm' "
        "CHECK (permission_mode_effective IN ('confirm','explicit','auto'))"
    )
    op.execute("UPDATE capability_requests SET permission_mode_effective=permission_mode")
    op.execute(
        "ALTER TABLE capability_requests ADD COLUMN retry_of_request_id "
        "TEXT REFERENCES capability_requests(id) ON DELETE SET NULL"
    )
    with op.batch_alter_table("capability_events") as batch:
        batch.drop_constraint("ck_capability_events_action", type_="check")
        batch.create_check_constraint(
            "ck_capability_events_action",
            "action IN ('requested','approved','denied','queued','started','completed','failed','cancelled','expired','replanned','retried')",
        )

    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Text(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "assistant_message_id",
            sa.Text(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "capability_request_id",
            sa.Text(),
            sa.ForeignKey("capability_requests.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), sa.ForeignKey("media_files.id", ondelete="SET NULL")),
        sa.Column("identity_state", sa.Text(), nullable=False, server_default="not_applicable"),
        sa.Column("safe_error", sa.Text()),
        sa.Column("retry_available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer()),
        sa.CheckConstraint("kind IN ('image','video')", name="ck_chat_attachments_kind"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','retried')",
            name="ck_chat_attachments_status",
        ),
        sa.CheckConstraint(
            "identity_state IN ('not_applicable','unconditioned','verified','unverified')",
            name="ck_chat_attachments_identity_state",
        ),
        sa.CheckConstraint("retry_available IN (0,1)", name="ck_chat_attachments_retry_available"),
    )
    op.create_index(
        "idx_chat_attachments_user_chat",
        "chat_attachments",
        ["user_id", "chat_id", "created_at"],
    )
    op.create_index(
        "idx_chat_attachments_message",
        "chat_attachments",
        ["assistant_message_id", "created_at"],
    )

    connection = op.get_bind()
    stamp = int(time.time())
    rows = []
    for user_id in connection.execute(sa.text("SELECT id FROM users")):
        for key, value in (
            ("image_confirmation_policy", "auto_explicit_request"),
            ("chat_blur_images", False),
        ):
            exists = connection.execute(
                sa.text("SELECT 1 FROM setting_values WHERE user_id=:user_id AND key=:key"),
                {"user_id": user_id[0], "key": key},
            ).first()
            if not exists:
                rows.append(
                    {
                        "user_id": user_id[0],
                        "key": key,
                        "value_type": "bool" if isinstance(value, bool) else "str",
                        "value_json": json.dumps(value, separators=(",", ":")),
                        "updated_at": stamp,
                    }
                )
    if rows:
        setting_values = sa.table(
            "setting_values",
            sa.column("user_id"),
            sa.column("key"),
            sa.column("value_type"),
            sa.column("value_json"),
            sa.column("updated_at"),
        )
        connection.execute(setting_values.insert(), rows)


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
