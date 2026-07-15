"""Add durable permissioned capability requests and audit history."""

from __future__ import annotations

from alembic import op
import json
import sqlalchemy as sa


revision = "0008_capability_framework"
down_revision = "0007_browser_v1_cutover"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM setting_values WHERE key='image_prompt_generation'"))
    for user_id, raw_preferences in connection.execute(sa.text("SELECT user_id,preferences_json FROM app_settings")):
        try:
            preferences = json.loads(raw_preferences or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(preferences, dict) or "image_prompt_generation" not in preferences:
            continue
        preferences.pop("image_prompt_generation", None)
        connection.execute(
            sa.text("UPDATE app_settings SET preferences_json=:preferences WHERE user_id=:user_id"),
            {
                "user_id": user_id,
                "preferences": json.dumps(preferences, separators=(",", ":")),
            },
        )

    op.create_table(
        "capability_requests",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=True),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("capability_key", sa.Text(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("permission_mode", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_confirmation','queued','running','completed','failed','cancelled','denied','expired')",
            name="ck_capability_requests_status",
        ),
        sa.CheckConstraint(
            "permission_mode IN ('confirm','explicit')",
            name="ck_capability_requests_permission_mode",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_id"], ["conversation_turns.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_capability_requests_user_idempotency"),
    )
    op.create_index(
        "idx_capability_requests_user_chat",
        "capability_requests",
        ["user_id", "chat_id", "requested_at"],
    )
    op.create_index(
        "idx_capability_requests_user_status",
        "capability_requests",
        ["user_id", "status", "requested_at"],
    )
    op.create_index("idx_capability_requests_turn", "capability_requests", ["turn_id"])

    op.create_table(
        "capability_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("capability_request_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('requested','approved','denied','queued','started','completed','failed','cancelled','expired')",
            name="ck_capability_events_action",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["capability_request_id"],
            ["capability_requests.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_capability_events_request_created",
        "capability_events",
        ["capability_request_id", "created_at"],
    )
    op.create_index("idx_capability_events_user_created", "capability_events", ["user_id", "created_at"])

    with op.batch_alter_table("async_jobs", recreate="always") as batch:
        batch.add_column(sa.Column("capability_request_id", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_async_jobs_capability_request",
            "capability_requests",
            ["capability_request_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_async_jobs_capability_request",
            ["capability_request_id"],
        )
    op.create_index(
        "idx_async_jobs_capability_request",
        "async_jobs",
        ["capability_request_id"],
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
