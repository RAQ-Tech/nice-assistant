"""Link a scene backlog entry to the request producing it.

Without this, an entry moved to `generating` has no way back. A restart in the
middle of a background picture would leave it in that state forever, which is
the one outcome the backlog is supposed to prevent: work that looks like it is
happening and is not.

Additive. Existing rows get NULL, which is correct - none of them were being
produced, because nothing produced them before this.
"""

from __future__ import annotations

from alembic import op


revision = "0030_scene_production_link"
down_revision = "0029_scene_proposal_role"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE persona_scene_backlog ADD COLUMN capability_request_id TEXT "
        "REFERENCES capability_requests(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX idx_scene_backlog_capability ON persona_scene_backlog(capability_request_id)")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
