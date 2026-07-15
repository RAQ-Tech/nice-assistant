"""Bind media plans and generated artifacts to reviewed persona identity inputs."""

from alembic import op
import sqlalchemy as sa


revision = "0013_identity_generation"
down_revision = "0012_resource_coordination"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_execution_plans") as batch:
        batch.add_column(sa.Column("persona_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("identity_profile_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("identity_profile_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("identity_reference_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("identity_reference_sha256", sa.Text(), nullable=True))
        batch.add_column(sa.Column("identity_conditioning_json", sa.Text(), nullable=False, server_default="{}"))
        batch.create_index("idx_media_plans_persona_created", ["user_id", "persona_id", "created_at"])
        batch.create_index("idx_media_plans_identity_reference", ["identity_reference_id"])

    with op.batch_alter_table("media_files") as batch:
        batch.add_column(sa.Column("generation_plan_id", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_media_files_generation_plan",
            "media_execution_plans",
            ["generation_plan_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("idx_media_files_generation_plan", ["generation_plan_id"])


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
