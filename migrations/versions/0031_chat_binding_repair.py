"""Repair chats whose workspace and persona disagree.

Until now a turn could retarget the chat it belonged to, so rows exist where the
persona is not a member of the workspace recorded beside it, and rows where a
persona is set with no workspace at all. Those chats are the reason ADR 0032
exists; leaving them inconsistent would mean the new invariant is true for new
chats and quietly false for old ones.

The rule is conservative and stated in `docs/conversation-context.md`: the
persona is kept and the workspace is corrected to one that persona actually
belongs to. The transcript was produced by that persona, so keeping the persona
keeps every reply attributable to whoever wrote it. Nothing is deleted, no
message is reattributed, and a chat whose persona no longer exists is left
exactly as it is.
"""

from __future__ import annotations

from alembic import op


revision = "0031_chat_binding_repair"
down_revision = "0030_scene_production_link"
branch_labels = None
depends_on = None


def upgrade():
    # A persona with no workspace beside it: adopt the persona's primary.
    op.execute(
        """
        UPDATE chats
           SET workspace_id = (SELECT workspace_id FROM personas WHERE personas.id = chats.persona_id)
         WHERE persona_id IS NOT NULL
           AND workspace_id IS NULL
        """
    )
    # A persona that is not a member of the workspace recorded beside it: keep
    # the persona, correct the workspace.
    op.execute(
        """
        UPDATE chats
           SET workspace_id = (SELECT workspace_id FROM personas WHERE personas.id = chats.persona_id)
         WHERE persona_id IS NOT NULL
           AND workspace_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1 FROM persona_workspace_links
                  WHERE persona_workspace_links.persona_id = chats.persona_id
                    AND persona_workspace_links.workspace_id = chats.workspace_id
               )
        """
    )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
