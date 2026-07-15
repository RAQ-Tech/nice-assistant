"""Add separately configured platform task models and content-free run audit records."""

from __future__ import annotations

from alembic import op
import secrets
import sqlalchemy as sa
import time


revision = "0009_task_models"
down_revision = "0008_capability_framework"
branch_labels = None
depends_on = None


PROFILE_DEFAULTS = {
    "title_generation": (512, 64, 30.0, 0.1, "deterministic"),
    "conversation_summary": (4096, 512, 90.0, 0.1, "skip"),
    "memory_extraction": (2048, 384, 60.0, 0.0, "fail"),
    "capability_planning": (2048, 384, 60.0, 0.0, "skip"),
}


def upgrade():
    op.create_table(
        "task_model_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("fallback_provider", sa.Text(), nullable=True),
        sa.Column("fallback_model", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("fallback_policy", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('title_generation','conversation_summary','memory_extraction','capability_planning')",
            name="ck_task_model_profiles_role",
        ),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_task_model_profiles_enabled"),
        sa.CheckConstraint(
            "fallback_policy IN ('deterministic','skip','fail')",
            name="ck_task_model_profiles_fallback_policy",
        ),
        sa.CheckConstraint(
            "max_input_tokens BETWEEN 128 AND 262144",
            name="ck_task_model_profiles_input_budget",
        ),
        sa.CheckConstraint(
            "max_output_tokens BETWEEN 16 AND 8192",
            name="ck_task_model_profiles_output_budget",
        ),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 600", name="ck_task_model_profiles_timeout"),
        sa.CheckConstraint("temperature BETWEEN 0 AND 2", name="ck_task_model_profiles_temperature"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "role", name="uq_task_model_profiles_user_role"),
    )
    op.create_index("idx_task_model_profiles_user", "task_model_profiles", ["user_id", "role"])

    op.create_table(
        "task_model_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=True),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("requested_provider", sa.Text(), nullable=True),
        sa.Column("requested_model", sa.Text(), nullable=True),
        sa.Column("executed_provider", sa.Text(), nullable=True),
        sa.Column("executed_model", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fallback_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("input_tokens_estimated", sa.Integer(), nullable=False),
        sa.Column("output_tokens_estimated", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "role IN ('title_generation','conversation_summary','memory_extraction','capability_planning')",
            name="ck_task_model_runs_role",
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','fallback','failed')",
            name="ck_task_model_runs_status",
        ),
        sa.CheckConstraint("fallback_used IN (0,1)", name="ck_task_model_runs_fallback_used"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_id"], ["conversation_turns.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_task_model_runs_user_started", "task_model_runs", ["user_id", "started_at"])
    op.create_index("idx_task_model_runs_user_role", "task_model_runs", ["user_id", "role", "started_at"])
    op.create_index("idx_task_model_runs_turn", "task_model_runs", ["turn_id"])

    connection = op.get_bind()
    stamp = int(time.time())
    setting_columns = {column["name"] for column in sa.inspect(connection).get_columns("app_settings")}
    if "global_default_model" in setting_columns:
        users = connection.execute(
            sa.text(
                "SELECT users.id,app_settings.global_default_model FROM users "
                "LEFT JOIN app_settings ON app_settings.user_id=users.id"
            )
        ).fetchall()
    else:
        users = [(row[0], None) for row in connection.execute(sa.text("SELECT id FROM users")).fetchall()]
    for user_id, initial_model in users:
        for role, defaults in PROFILE_DEFAULTS.items():
            max_input, max_output, timeout, temperature, fallback_policy = defaults
            connection.execute(
                sa.text(
                    "INSERT INTO task_model_profiles("
                    "id,user_id,role,provider,model,enabled,max_input_tokens,max_output_tokens,"
                    "timeout_seconds,temperature,fallback_policy,created_at,updated_at"
                    ") VALUES(:id,:user_id,:role,'ollama',:model,1,:max_input,:max_output,:timeout,"
                    ":temperature,:fallback_policy,:stamp,:stamp)"
                ),
                {
                    "id": secrets.token_hex(12),
                    "user_id": user_id,
                    "role": role,
                    "model": initial_model,
                    "max_input": max_input,
                    "max_output": max_output,
                    "timeout": timeout,
                    "temperature": temperature,
                    "fallback_policy": fallback_policy,
                    "stamp": stamp,
                },
            )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
