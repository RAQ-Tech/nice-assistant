"""Record which shipped template a workflow resource came from.

A graph installed from a template and a graph an operator exported by hand look
identical once saved, so nothing could tell an operator that a newer version of
the template they used exists - or, more importantly, avoid rewriting a graph
they have since tuned.

Two nullable columns rather than a note, because this is read by code: a note is
prose nobody can query, and `external_id` already means something else. Null is
the normal state; it says the graph did not come from here.
"""

from __future__ import annotations

from alembic import op


revision = "0036_workflow_template_provenance"
down_revision = "0035_persona_voice_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE media_catalog_resources ADD COLUMN source_template_id TEXT")
    op.execute("ALTER TABLE media_catalog_resources ADD COLUMN source_template_version INTEGER")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
