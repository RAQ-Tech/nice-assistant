"""Move a persona's voice settings out of provider-named columns.

A persona carried nine columns for text-to-speech: three unqualified, three
suffixed `_openai`, three suffixed `_local`. Adding a third provider meant three
more columns and another migration, and a provider nobody had added a column for
resolved silently to nothing. That is persona data shaped by whichever provider
happened to be configured first.

They become one object keyed by provider, with the unqualified trio kept as a
`default` that any provider falls back to. Backfilled from what is there, then
the old columns are dropped: leaving nine unread columns behind would preserve
the exact shape this removes.
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0035_persona_voice_preferences"
down_revision = "0034_preset_signals"
branch_labels = None
depends_on = None

LEGACY_COLUMNS = (
    "preferred_voice",
    "preferred_tts_model",
    "preferred_tts_speed",
    "preferred_voice_openai",
    "preferred_tts_model_openai",
    "preferred_tts_speed_openai",
    "preferred_voice_local",
    "preferred_tts_model_local",
    "preferred_tts_speed_local",
)


def _entry(voice, model, speed) -> dict:
    values = {}
    if voice:
        values["voice"] = str(voice)
    if model:
        values["model"] = str(model)
    if speed:
        values["speed"] = str(speed)
    return values


def upgrade():
    connection = op.get_bind()
    op.execute("ALTER TABLE personas ADD COLUMN voice_preferences_json TEXT NOT NULL DEFAULT '{}'")
    rows = connection.execute(sa.text(f"SELECT id, {', '.join(LEGACY_COLUMNS)} FROM personas")).fetchall()
    for row in rows:
        values = dict(zip(("id", *LEGACY_COLUMNS), row, strict=True))
        preferences = {}
        # The unqualified trio was whatever provider was configured when it was
        # set, so it becomes the fallback rather than being assigned to one.
        default = _entry(values["preferred_voice"], values["preferred_tts_model"], values["preferred_tts_speed"])
        if default:
            preferences["default"] = default
        for provider in ("openai", "local"):
            entry = _entry(
                values[f"preferred_voice_{provider}"],
                values[f"preferred_tts_model_{provider}"],
                values[f"preferred_tts_speed_{provider}"],
            )
            if entry:
                preferences[provider] = entry
        if preferences:
            connection.execute(
                sa.text("UPDATE personas SET voice_preferences_json = :value WHERE id = :id"),
                {"value": json.dumps(preferences, separators=(",", ":"), ensure_ascii=False), "id": values["id"]},
            )
    for column in LEGACY_COLUMNS:
        op.execute(f"ALTER TABLE personas DROP COLUMN {column}")


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
