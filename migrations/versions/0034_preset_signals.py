"""Record what happens to a picture after it is made.

Which preset produced a picture was only ever recorded inside the plan's
explanation JSON. That is fine for reading one plan and wrong for counting
across many, so the preset gets its own column and the existing rows are
backfilled from the JSON they already hold.

The counts table holds explicit signals only: a picture deliberately kept, a
picture sent again, a picture removed. Generating a picture is not a signal -
the platform chose the preset, so counting it would be the platform scoring its
own homework.
"""

from __future__ import annotations

from alembic import op


revision = "0034_preset_signals"
down_revision = "0033_attachment_frames"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE media_execution_plans ADD COLUMN preset_id TEXT")
    # Every existing coordinator plan already names its preset in the
    # explanation it wrote at the time.
    op.execute(
        """
        UPDATE media_execution_plans
           SET preset_id = json_extract(explanation_json, '$.preset.id')
         WHERE preset_id IS NULL
           AND explanation_json IS NOT NULL
           AND json_valid(explanation_json)
        """
    )
    op.execute("CREATE INDEX idx_media_plans_preset ON media_execution_plans(preset_id)")
    op.execute(
        """
        CREATE TABLE media_preset_signals (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            persona_id TEXT,
            preset_id TEXT NOT NULL,
            kept INTEGER NOT NULL DEFAULT 0,
            sent_again INTEGER NOT NULL DEFAULT 0,
            removed INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_preset_signals_owner_persona_preset "
        "ON media_preset_signals(user_id, COALESCE(persona_id,''), preset_id)"
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
