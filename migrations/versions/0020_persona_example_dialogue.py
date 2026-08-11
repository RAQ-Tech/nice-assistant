"""Add persona example dialogue."""

from __future__ import annotations

from alembic import op


revision = "0020_persona_example_dialogue"
down_revision = "0019_persona_character_card"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE personas ADD COLUMN card_example_dialogue TEXT")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
