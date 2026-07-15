"""Add durable, consent-bound persona visual identity records."""

from alembic import op
import sqlalchemy as sa


revision = "0011_persona_identity"
down_revision = "0010_media_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "identity_validation_settings",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="15"),
        sa.Column("last_validation_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("provider IN ('disabled','compreface')", name="ck_identity_settings_provider"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 120", name="ck_identity_settings_timeout"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "persona_visual_identities",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("consent_status", sa.Text(), nullable=False, server_default="not_granted"),
        sa.Column("appearance_description", sa.Text(), nullable=True),
        sa.Column("acceptance_threshold", sa.Float(), nullable=False, server_default="0.78"),
        sa.Column("max_generation_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("failure_policy", sa.Text(), nullable=False, server_default="block_claim"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_validation_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consent_granted_at", sa.Integer(), nullable=True),
        sa.Column("consent_withdrawn_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('draft','active','disabled')", name="ck_visual_identity_status"),
        sa.CheckConstraint(
            "consent_status IN ('not_granted','granted','withdrawn')", name="ck_visual_identity_consent"
        ),
        sa.CheckConstraint("acceptance_threshold BETWEEN 0 AND 1", name="ck_visual_identity_threshold"),
        sa.CheckConstraint("max_generation_attempts BETWEEN 1 AND 10", name="ck_visual_identity_attempts"),
        sa.CheckConstraint(
            "failure_policy IN ('block_claim','show_unverified')", name="ck_visual_identity_failure_policy"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "persona_id", name="uq_visual_identity_owner_persona"),
    )
    op.create_index("idx_visual_identity_owner_status", "persona_visual_identities", ["user_id", "status"])
    op.create_table(
        "persona_identity_references",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("identity_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("source_media_id", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("is_primary", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consent_attested_at", sa.Integer(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "provenance IN ('user_upload','generated_approved','imported')", name="ck_identity_reference_provenance"
        ),
        sa.CheckConstraint(
            "review_status IN ('pending','approved','rejected','deleted')", name="ck_identity_reference_review"
        ),
        sa.CheckConstraint("is_primary IN (0,1)", name="ck_identity_reference_primary"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_id"], ["persona_visual_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_media_id"], ["media_files.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_identity_reference_profile_status",
        "persona_identity_references",
        ["identity_id", "review_status", "created_at"],
    )
    op.create_index(
        "idx_identity_reference_owner_persona",
        "persona_identity_references",
        ["user_id", "persona_id", "created_at"],
    )
    op.create_table(
        "persona_identity_validations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("identity_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("candidate_media_id", sa.Text(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("created_order", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("matched_reference_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("failure_policy", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("source_face_count", sa.Integer(), nullable=True),
        sa.Column("target_face_count", sa.Integer(), nullable=True),
        sa.Column("provider_version", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','passed','failed','error','cancelled')",
            name="ck_identity_validation_status",
        ),
        sa.CheckConstraint(
            "failure_policy IN ('block_claim','show_unverified')",
            name="ck_identity_validation_failure_policy",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_id"], ["persona_visual_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["async_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_reference_id"], ["persona_identity_references.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("job_id", name="uq_identity_validation_job"),
        sa.UniqueConstraint("identity_id", "sequence_number", name="uq_identity_validation_sequence"),
        sa.UniqueConstraint("user_id", "created_order", name="uq_identity_validation_owner_order"),
    )
    op.create_index(
        "idx_identity_validation_owner_persona",
        "persona_identity_validations",
        ["user_id", "persona_id", "created_at"],
    )
    op.create_index(
        "idx_identity_validation_candidate",
        "persona_identity_validations",
        ["candidate_media_id", "created_at"],
    )
    op.create_index(
        "idx_identity_validation_candidate_order",
        "persona_identity_validations",
        ["candidate_media_id", "created_order"],
    )
    op.create_table(
        "persona_identity_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("identity_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("reference_id", sa.Text(), nullable=True),
        sa.Column("validation_id", sa.Text(), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_id"], ["persona_visual_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reference_id"], ["persona_identity_references.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["validation_id"], ["persona_identity_validations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("identity_id", "sequence_number", name="uq_identity_event_sequence"),
    )
    op.create_index("idx_identity_event_profile_created", "persona_identity_events", ["identity_id", "created_at"])
    op.create_index("idx_identity_event_owner_created", "persona_identity_events", ["user_id", "created_at"])


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
