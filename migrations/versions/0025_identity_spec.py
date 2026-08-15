"""Record how a persona's resemblance is produced, and demote comparison.

ADR 0031. Resemblance comes from a declared conditioning mechanism; a
comparison afterwards is advisory measurement. The comparison-driven retry loop
is switched off for every existing profile, because resampling until a check
passes was never the control it appeared to be.

Reviewed references, consent, validations, and completed plans are untouched.
"""

from __future__ import annotations

from alembic import op


revision = "0025_identity_spec"
down_revision = "0024_media_generation_presets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE persona_visual_identities "
        "ADD COLUMN conditioning_mechanism TEXT NOT NULL DEFAULT 'reference_adapter'"
    )
    op.execute(
        "ALTER TABLE persona_visual_identities ADD COLUMN conditioning_parameters_json TEXT NOT NULL DEFAULT '{}'"
    )
    op.execute("ALTER TABLE persona_visual_identities ADD COLUMN comparison_retry_enabled INTEGER NOT NULL DEFAULT 0")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
