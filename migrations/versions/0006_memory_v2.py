"""Add reviewable, auditable Memory v2 with scoped full-text search."""

from __future__ import annotations

import json
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "0006_memory_v2"
down_revision = "0005_causal_context"
branch_labels = None
depends_on = None


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def upgrade():
    with op.batch_alter_table("memories", recreate="always") as batch:
        batch.add_column(sa.Column("normalized_content", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", sa.Text(), nullable=False, server_default="active"))
        batch.add_column(sa.Column("source_type", sa.Text(), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("source_message_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_turn_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("supersedes_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("extractor_provider", sa.Text(), nullable=True))
        batch.add_column(sa.Column("extractor_model", sa.Text(), nullable=True))
        batch.add_column(sa.Column("extractor_version", sa.Text(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reviewed_at", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("forgotten_at", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_memories_source_message",
            "messages",
            ["source_message_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_memories_source_turn",
            "conversation_turns",
            ["source_turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_memories_supersedes",
            "memories",
            ["supersedes_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_memories_tier",
            "tier IN ('global','workspace','persona','chat')",
        )
        batch.create_check_constraint(
            "ck_memories_status",
            "status IN ('pending','active','rejected','forgotten','superseded')",
        )
        batch.create_check_constraint(
            "ck_memories_source_type",
            "source_type IN ('legacy','manual','conversation','edit')",
        )
        batch.create_check_constraint(
            "ck_memories_confidence",
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        )

    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        "SELECT id,user_id,tier,tier_ref_id,content,created_at FROM memories "
        "ORDER BY user_id,tier,tier_ref_id,created_at,id"
    ).fetchall()
    grouped: dict[tuple[str, str, str, str], list[tuple]] = {}
    for row in rows:
        memory_id, user_id, tier, tier_ref_id, content, created_at = row
        normalized = _normalize(content)
        bind.exec_driver_sql(
            "UPDATE memories SET normalized_content=?,updated_at=? WHERE id=?",
            (normalized, created_at, memory_id),
        )
        grouped.setdefault((user_id, tier, tier_ref_id or "", normalized), []).append(row)

    for duplicates in grouped.values():
        previous_id = None
        for index, row in enumerate(duplicates):
            memory_id, _user_id, _tier, _tier_ref_id, _content, created_at = row
            if previous_id is not None:
                bind.exec_driver_sql(
                    "UPDATE memories SET supersedes_id=? WHERE id=?",
                    (previous_id, memory_id),
                )
            if index < len(duplicates) - 1:
                bind.exec_driver_sql(
                    "UPDATE memories SET status='superseded',reviewed_at=?,updated_at=? WHERE id=?",
                    (created_at, created_at, memory_id),
                )
            previous_id = memory_id

    op.create_index(
        "idx_memories_user_status_updated",
        "memories",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "idx_memories_user_scope_status",
        "memories",
        ["user_id", "tier", "tier_ref_id", "status"],
    )
    op.create_index("idx_memories_source_turn", "memories", ["source_turn_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_memories_live_normalized_scope "
        "ON memories(user_id,tier,IFNULL(tier_ref_id,''),normalized_content) "
        "WHERE status IN ('pending','active')"
    )

    op.create_table(
        "memory_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("related_memory_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("undone_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "action IN ('migrated','created','candidate_created','approved','rejected','forgotten','edited',"
            "'superseded','scope_archived','undo_edit','undo_approved','undo_rejected','undo_forgotten')",
            name="ck_memory_events_action",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_memory_id"], ["memories.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_memory_events_memory_created", "memory_events", ["memory_id", "created_at"])
    op.create_index("idx_memory_events_user_created", "memory_events", ["user_id", "created_at"])
    for row in rows:
        memory_id, user_id, _tier, _tier_ref_id, _content, created_at = row
        status = bind.exec_driver_sql("SELECT status FROM memories WHERE id=?", (memory_id,)).fetchone()[0]
        bind.exec_driver_sql(
            "INSERT INTO memory_events(id,user_id,memory_id,action,from_status,to_status,snapshot_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                f"migration-{memory_id}",
                user_id,
                memory_id,
                "migrated",
                None,
                status,
                json.dumps({"source_type": "legacy"}, separators=(",", ":")),
                created_at,
            ),
        )

    op.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "memory_id UNINDEXED,user_id UNINDEXED,content,tokenize='unicode61 remove_diacritics 2')"
    )
    op.execute("INSERT INTO memory_fts(memory_id,user_id,content) SELECT id,user_id,content FROM memories")
    op.execute(
        "CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN "
        "INSERT INTO memory_fts(memory_id,user_id,content) VALUES(new.id,new.user_id,new.content); END"
    )
    op.execute(
        "CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN "
        "DELETE FROM memory_fts WHERE memory_id=old.id; END"
    )
    op.execute(
        "CREATE TRIGGER memories_fts_update AFTER UPDATE OF content,user_id ON memories BEGIN "
        "DELETE FROM memory_fts WHERE memory_id=old.id; "
        "INSERT INTO memory_fts(memory_id,user_id,content) VALUES(new.id,new.user_id,new.content); END"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
