"""Raise a stored 4096-token context window to 8192.

The default was 4096, which left 3328 tokens for the system prompt, the persona
card, its lorebook, memories and the whole conversation once the reply
allowance and the safety margin were reserved. That is tight enough that saved
context gets dropped on ordinary turns.

Raising the default alone would not move anybody, because the browser sends
every setting on every save - so a default that was never chosen is stored as
though it were. That is a real seam and it means an unchanged default can only
be corrected here.

So this changes exactly the accounts that are still on the old default, and
leaves every other value alone. Somebody who deliberately set 2048 because
their hardware wants it keeps 2048; somebody who set 16384 keeps that.
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0039_wider_default_context_window"
down_revision = "0038_scene_from_message_role"
branch_labels = None
depends_on = None

# Written out rather than imported. A migration records what happened on the
# day it ran; if it followed a constant, changing that constant later would
# silently change what an already-applied migration claims to have done.
OLD_DEFAULT = 4096
NEW_DEFAULT = 8192
KEY = "models_context_window_tokens"


def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT user_id, preferences_json FROM app_settings")).fetchall()
    for user_id, raw in rows:
        try:
            preferences = json.loads(raw or "{}")
        except ValueError:
            continue
        if not isinstance(preferences, dict):
            continue
        stored = preferences.get(KEY)
        if stored is None:
            # Never stored, so the new default already applies.
            continue
        try:
            current = int(str(stored).strip())
        except (TypeError, ValueError):
            continue
        if current != OLD_DEFAULT:
            # A deliberate choice. Not this migration's business.
            continue
        preferences[KEY] = str(NEW_DEFAULT)
        bind.execute(
            sa.text("UPDATE app_settings SET preferences_json = :preferences WHERE user_id = :user_id"),
            {
                "preferences": json.dumps(preferences, separators=(",", ":"), ensure_ascii=False),
                "user_id": user_id,
            },
        )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
