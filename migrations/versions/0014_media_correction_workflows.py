"""Add durable media generation, validation, and correction attempts."""

from alembic import op
import sqlalchemy as sa


revision = "0014_media_correction_workflows"
down_revision = "0013_identity_generation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_generation_attempts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "media_plan_id",
            sa.Text(),
            sa.ForeignKey("media_execution_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), sa.ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "validation_id",
            sa.Text(),
            sa.ForeignKey("persona_identity_validations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_media_id",
            sa.Text(),
            sa.ForeignKey("media_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_resource_id",
            sa.Text(),
            sa.ForeignKey("media_catalog_resources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "operation IN ('generate','inpaint','outpaint','image_to_image')",
            name="ck_media_attempt_operation",
        ),
        sa.CheckConstraint(
            "status IN ('running','passed','failed','unverified','error','cancelled')",
            name="ck_media_attempt_status",
        ),
        sa.CheckConstraint("attempt_number BETWEEN 1 AND 10", name="ck_media_attempt_number"),
        sa.UniqueConstraint("media_plan_id", "attempt_number", name="uq_media_attempt_plan_number"),
    )
    op.create_index(
        "idx_media_attempt_owner_started",
        "media_generation_attempts",
        ["user_id", "started_at"],
    )
    op.create_index(
        "idx_media_attempt_plan_status",
        "media_generation_attempts",
        ["media_plan_id", "status", "attempt_number"],
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
