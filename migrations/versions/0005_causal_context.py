"""Add causal turn ordering and durable context summaries."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0005_causal_context"
down_revision = "0004_conversation_turns"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    linked_jobs = bind.exec_driver_sql("SELECT id,turn_id FROM async_jobs WHERE turn_id IS NOT NULL").fetchall()

    op.add_column(
        "chats",
        sa.Column("last_turn_sequence", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("previous_summary_id", sa.Text(), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("through_message_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("source_digest", sa.Text(), nullable=False),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_summary_id"], ["conversation_summaries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["through_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", "sequence_number", name="uq_conversation_summaries_chat_sequence"),
    )
    op.create_index(
        "idx_conversation_summaries_user_chat",
        "conversation_summaries",
        ["user_id", "chat_id", "created_at"],
    )

    with op.batch_alter_table("conversation_turns", recreate="always") as batch:
        batch.add_column(sa.Column("sequence_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("context_summary_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("context_window_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("prompt_budget_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("prompt_tokens_estimated", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("prompt_tokens_actual", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("included_message_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("omitted_message_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("included_memory_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("omitted_memory_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("context_degraded_reason", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_conversation_turns_summary",
            "conversation_summaries",
            ["context_summary_id"],
            ["id"],
            ondelete="SET NULL",
        )

    chats = bind.exec_driver_sql("SELECT id FROM chats ORDER BY id").fetchall()
    for (chat_id,) in chats:
        turns = bind.exec_driver_sql(
            "SELECT id FROM conversation_turns WHERE chat_id=? ORDER BY created_at,id",
            (chat_id,),
        ).fetchall()
        for sequence, (turn_id,) in enumerate(turns, start=1):
            bind.exec_driver_sql(
                "UPDATE conversation_turns SET sequence_number=? WHERE id=?",
                (sequence, turn_id),
            )
        bind.exec_driver_sql(
            "UPDATE chats SET last_turn_sequence=? WHERE id=?",
            (len(turns), chat_id),
        )

    with op.batch_alter_table("conversation_turns", recreate="always") as batch:
        batch.alter_column("sequence_number", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_conversation_turns_chat_sequence", ["chat_id", "sequence_number"])

    for job_id, turn_id in linked_jobs:
        bind.exec_driver_sql(
            "UPDATE async_jobs SET turn_id=? WHERE id=?",
            (turn_id, job_id),
        )

    inspector = sa.inspect(bind)
    chat_columns = {column["name"] for column in inspector.get_columns("chats")}
    setting_columns = {column["name"] for column in inspector.get_columns("app_settings")}
    tables = set(inspector.get_table_names())
    if "memory_mode" in chat_columns:
        bind.exec_driver_sql("UPDATE chats SET memory_mode='saved' WHERE memory_mode IN ('auto','manual')")
    if "default_memory_mode" in setting_columns:
        bind.exec_driver_sql(
            "UPDATE app_settings SET default_memory_mode='saved' WHERE default_memory_mode IN ('auto','manual')"
        )
    if "setting_values" in tables:
        bind.exec_driver_sql("DELETE FROM setting_values WHERE key='memory_auto_save_user_facts'")
    rows = (
        bind.exec_driver_sql(
            "SELECT user_id,preferences_json FROM app_settings WHERE preferences_json IS NOT NULL"
        ).fetchall()
        if "preferences_json" in setting_columns
        else []
    )
    for user_id, raw in rows:
        try:
            preferences = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(preferences, dict) and "memory_auto_save_user_facts" in preferences:
            preferences.pop("memory_auto_save_user_facts", None)
            bind.exec_driver_sql(
                "UPDATE app_settings SET preferences_json=? WHERE user_id=?",
                (json.dumps(preferences, separators=(",", ":")), user_id),
            )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
