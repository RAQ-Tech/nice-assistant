"""Add an explicit persona conditioning fallback policy."""

from alembic import op
import sqlalchemy as sa


revision = "0016_identity_fallback"
down_revision = "0015_media_provider_bootstrap"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("persona_visual_identities") as batch:
        batch.add_column(
            sa.Column(
                "conditioning_fallback",
                sa.Text(),
                nullable=False,
                server_default="allow_unconditioned",
            )
        )
        batch.create_check_constraint(
            "ck_visual_identity_conditioning_fallback",
            "conditioning_fallback IN ('allow_unconditioned','require_conditioning')",
        )
    with op.batch_alter_table("capability_events") as batch:
        batch.drop_constraint("ck_capability_events_action", type_="check")
        batch.create_check_constraint(
            "ck_capability_events_action",
            "action IN ('requested','approved','denied','queued','started','completed','failed','cancelled','expired','replanned')",
        )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
