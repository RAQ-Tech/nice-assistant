"""Admit local-video as a media resource provider.

Video became local-only by decision on 2026-08-26: Sora's API shuts down on
2026-09-24 and no surviving cloud video service accepts this product's
content. Local video runs through the operator's own ComfyUI, and its catalog
rows carry the provider key this constraint previously refused.

SQLite cannot alter a CHECK constraint in place, so the batch rewrite
recreates the table under the widened rule. Every existing row already
satisfies it.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0040_local_video_provider"
down_revision = "0039_wider_default_context_window"
branch_labels = None
depends_on = None

_OLD = "provider_key IN ('openai-image','local-image','openai-video')"
_NEW = "provider_key IN ('openai-image','local-image','openai-video','local-video')"


def upgrade() -> None:
    with op.batch_alter_table("media_catalog_resources") as batch:
        batch.drop_constraint("ck_media_resource_provider", type_="check")
        batch.create_check_constraint("ck_media_resource_provider", _NEW)


def downgrade() -> None:
    # A local-video row cannot satisfy the narrower rule; refuse rather than
    # silently deleting an operator's catalog entries.
    bind = op.get_bind()
    remaining = bind.execute(
        sa.text("SELECT COUNT(*) FROM media_catalog_resources WHERE provider_key = 'local-video'")
    ).scalar()
    if remaining:
        raise RuntimeError("Remove local-video catalog resources before downgrading.")
    with op.batch_alter_table("media_catalog_resources") as batch:
        batch.drop_constraint("ck_media_resource_provider", type_="check")
        batch.create_check_constraint("ck_media_resource_provider", _OLD)
