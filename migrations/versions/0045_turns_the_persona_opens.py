"""A turn the persona opens, with no message in front of it.

A new conversation used to wait for the first word. Now the persona can speak
first (ADR 0052), and that reply answers nothing anybody said, so a turn's user
message becomes optional. Every existing turn keeps the message it has.

SQLite cannot drop NOT NULL in place, so the table is rebuilt. Four tables point
at it, and with foreign keys on - which they are here - dropping the old table
sets each of their links to NULL. Every link is remembered first and put back
afterwards, so no memory, job, picture request or task-model run loses its turn.
"""

from __future__ import annotations

from alembic import op


revision = "0045_turns_the_persona_opens"
down_revision = "0044_persistent_sessions"
branch_labels = None
depends_on = None

LINKS = (
    ("memories", "source_turn_id"),
    ("capability_requests", "turn_id"),
    ("async_jobs", "turn_id"),
    ("task_model_runs", "turn_id"),
)
COLUMNS = (
    "id, user_id, chat_id, user_message_id, assistant_message_id, provider, model, status, error_code, "
    "error_message, created_at, started_at, completed_at, sequence_number, context_summary_id, "
    "context_window_tokens, prompt_budget_tokens, prompt_tokens_estimated, prompt_tokens_actual, "
    "included_message_count, omitted_message_count, included_memory_count, omitted_memory_count, "
    "context_degraded_reason"
)


def _rebuild(user_message: str) -> None:
    for table, column in LINKS:
        op.execute(
            f"CREATE TEMP TABLE kept_{table} AS SELECT id, {column} AS turn_id FROM {table} WHERE {column} IS NOT NULL"
        )
    op.execute(
        f"""
        CREATE TABLE conversation_turns_rebuilt (
            id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            user_message_id TEXT{user_message},
            assistant_message_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            completed_at INTEGER,
            sequence_number INTEGER NOT NULL,
            context_summary_id TEXT,
            context_window_tokens INTEGER,
            prompt_budget_tokens INTEGER,
            prompt_tokens_estimated INTEGER,
            prompt_tokens_actual INTEGER,
            included_message_count INTEGER,
            omitted_message_count INTEGER,
            included_memory_count INTEGER,
            omitted_memory_count INTEGER,
            context_degraded_reason TEXT,
            PRIMARY KEY (id),
            CONSTRAINT ck_conversation_turns_status
                CHECK (status IN ('queued','running','completed','failed','cancelled')),
            CONSTRAINT fk_conversation_turns_summary FOREIGN KEY(context_summary_id)
                REFERENCES conversation_summaries (id) ON DELETE SET NULL,
            CONSTRAINT uq_conversation_turns_chat_sequence UNIQUE (chat_id, sequence_number),
            FOREIGN KEY(assistant_message_id) REFERENCES messages (id) ON DELETE SET NULL,
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE,
            FOREIGN KEY(user_message_id) REFERENCES messages (id) ON DELETE CASCADE,
            UNIQUE (user_message_id),
            UNIQUE (assistant_message_id)
        )
        """
    )
    op.execute(f"INSERT INTO conversation_turns_rebuilt ({COLUMNS}) SELECT {COLUMNS} FROM conversation_turns")
    op.execute("DROP TABLE conversation_turns")
    op.execute("ALTER TABLE conversation_turns_rebuilt RENAME TO conversation_turns")
    op.execute("CREATE INDEX idx_conversation_turns_user_chat ON conversation_turns (user_id, chat_id, created_at)")
    op.execute("CREATE INDEX idx_conversation_turns_user_status ON conversation_turns (user_id, status, created_at)")
    for table, column in LINKS:
        op.execute(
            f"UPDATE {table} SET {column} = (SELECT kept.turn_id FROM kept_{table} AS kept WHERE kept.id = {table}.id) "
            f"WHERE id IN (SELECT id FROM kept_{table})"
        )
        op.execute(f"DROP TABLE kept_{table}")


def upgrade() -> None:
    _rebuild("")


def downgrade() -> None:
    # A turn the persona opened has no message to belong to in the older
    # schema. Its reply stays, as a message outside any turn.
    op.execute("DELETE FROM conversation_turns WHERE user_message_id IS NULL")
    _rebuild(" NOT NULL")
