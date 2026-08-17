"""Give a memory a vector, so it can be found by a question that shares no words.

Retrieval was keyword search plus recency. "What do I drive" never found "owns a
2019 Tacoma", because the two share nothing to match on. A vector is what makes
them comparable.

Three columns on the memory itself rather than a table beside it: a memory has
exactly one vector, so a join would buy nothing. The model name is stored with
it because vectors from different models are not comparable, and this is how a
stale one is recognised and recomputed instead of quietly scoring as unrelated.
"""

from __future__ import annotations

from alembic import op


revision = "0037_memory_embeddings"
down_revision = "0036_workflow_template_provenance"
branch_labels = None
depends_on = None


def upgrade():
    # Null everywhere to begin with. Nothing is embedded until the model is
    # configured and the backfill has run, and retrieval keeps working on
    # keywords in the meantime.
    op.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
    op.execute("ALTER TABLE memories ADD COLUMN embedding_model TEXT")
    op.execute("ALTER TABLE memories ADD COLUMN embedding_updated_at INTEGER")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_embedding_pending "
        "ON memories (user_id, status) WHERE embedding IS NULL"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
