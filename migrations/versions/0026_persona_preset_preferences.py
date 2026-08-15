"""Record which presets are known to work for a persona.

Additive and empty by default: an existing persona expresses no preference, and
inventing one would claim knowledge nobody recorded.
"""

from __future__ import annotations

from alembic import op


revision = "0026_persona_preset_preferences"
down_revision = "0025_persona_image_library"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE persona_visual_identities ADD COLUMN preferred_preset_ids_json TEXT NOT NULL DEFAULT '[]'")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
