"""Let lore keys match common English word forms."""

from __future__ import annotations

from alembic import op


revision = "0022_lore_word_forms"
down_revision = "0021_persona_lorebooks"
branch_labels = None
depends_on = None


def upgrade():
    # Defaulting existing entries to enabled only makes them fire more often, which is the
    # point: a key of "sister" silently missing "sisters" is the common authoring surprise.
    op.execute(
        "ALTER TABLE persona_lore_entries ADD COLUMN match_word_forms "
        "INTEGER NOT NULL DEFAULT 1 CHECK (match_word_forms IN (0,1))"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
