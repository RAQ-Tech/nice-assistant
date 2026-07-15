"""Add durable conversation turns and constrained job state."""

from alembic import op
import sqlalchemy as sa


revision = "0004_conversation_turns"
down_revision = "0003_enforce_relationships"
branch_labels = None
depends_on = None


TERMINAL_STATES = "'queued','running','completed','failed','cancelled'"


def upgrade():
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("user_message_id", sa.Text(), nullable=False, unique=True),
        sa.Column("assistant_message_id", sa.Text(), nullable=True, unique=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            f"status IN ({TERMINAL_STATES})",
            name="ck_conversation_turns_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["messages.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_conversation_turns_user_chat",
        "conversation_turns",
        ["user_id", "chat_id", "created_at"],
    )
    op.create_index(
        "idx_conversation_turns_user_status",
        "conversation_turns",
        ["user_id", "status", "created_at"],
    )

    with op.batch_alter_table("async_jobs", recreate="always") as batch:
        batch.add_column(sa.Column("turn_id", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_turn",
            "conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_async_jobs_turn_id", ["turn_id"])
        batch.create_check_constraint(
            "ck_async_jobs_status",
            f"status IN ({TERMINAL_STATES})",
        )
    op.create_index("idx_async_jobs_turn", "async_jobs", ["turn_id"])


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
