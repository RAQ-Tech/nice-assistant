"""Add the persona character card fields."""

from __future__ import annotations

from alembic import op


revision = "0019_persona_character_card"
down_revision = "0018_human_image_delivery"
branch_labels = None
depends_on = None


def upgrade():
    # Example dialogue is a separate budgeted section and arrives with its own phase; adding
    # its column here would create a stored field that nothing reads.
    for column in ("card_definition", "card_personality", "card_style", "card_behavior"):
        op.execute(f"ALTER TABLE personas ADD COLUMN {column} TEXT")
    op.execute("ALTER TABLE personas ADD COLUMN card_token_estimate INTEGER NOT NULL DEFAULT 0")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
