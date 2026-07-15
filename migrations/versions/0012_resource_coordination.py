"""Add truthful shared-resource coordination policy and audit records."""

from alembic import op
import sqlalchemy as sa


revision = "0012_resource_coordination"
down_revision = "0011_persona_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "resource_coordination_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("reserve_vram_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("max_wait_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("poll_interval_seconds", sa.Float(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_resource_coordination_singleton"),
        sa.CheckConstraint("mode IN ('disabled','observe','managed')", name="ck_resource_coordination_mode"),
        sa.CheckConstraint("reserve_vram_mb BETWEEN 0 AND 131072", name="ck_resource_coordination_reserve"),
        sa.CheckConstraint("max_wait_seconds BETWEEN 1 AND 3600", name="ck_resource_coordination_wait"),
        sa.CheckConstraint("poll_interval_seconds BETWEEN 0.25 AND 60", name="ck_resource_coordination_poll"),
    )
    op.create_table(
        "resource_control_authorizations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("endpoint_fingerprint", sa.Text(), nullable=False),
        sa.Column("exclusive_control", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allow_release", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("authorized_by_user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("provider IN ('ollama','comfyui','automatic1111')", name="ck_resource_control_provider"),
        sa.CheckConstraint("exclusive_control IN (0,1)", name="ck_resource_control_exclusive"),
        sa.CheckConstraint("allow_release IN (0,1)", name="ck_resource_control_release"),
        sa.ForeignKeyConstraint(["authorized_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider", "endpoint_fingerprint", name="uq_resource_control_endpoint"),
    )
    op.create_index("idx_resource_control_provider", "resource_control_authorizations", ["provider", "updated_at"])
    op.create_table(
        "resource_coordination_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("endpoint_fingerprint", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('waiting','admitted','released','release_failed','timed_out','cancelled')",
            name="ck_resource_coordination_event_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('info','success','failed','cancelled')",
            name="ck_resource_coordination_event_outcome",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["async_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_resource_coordination_events_created", "resource_coordination_events", ["created_at"])
    op.create_index("idx_resource_coordination_events_job", "resource_coordination_events", ["job_id", "created_at"])


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
