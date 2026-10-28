"""Move a stored WAV speech format to MP3.

A reply spoken in WAV cannot start until the whole recording exists: WAV
carries its length in a header nothing can fill in halfway through. Streaming
speech (ADR 0037) and speaking while the reply is written (ADR 0042) both skip
it, silently, so every account still on the old default was waiting for the
whole reply to be synthesized and never knew why.

The browser sends every setting on every save, so a default that was never
chosen is stored as though it were - the same seam migration 0039 met. WAV
offers nothing a person would choose it for here: the speech is played and
kept for replay, and MP3 does both, sooner and smaller. So this moves exactly
the accounts on WAV and leaves every other format alone.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0042_streamable_default_speech_format"
down_revision = "0041_remove_retired_cloud_video"
branch_labels = None
depends_on = None

# Written out rather than imported. A migration records what happened on the
# day it ran; if it followed a constant, changing that constant later would
# silently change what an already-applied migration claims to have done.
OLD_DEFAULT = "wav"
NEW_DEFAULT = "mp3"


def upgrade() -> None:
    bind = op.get_bind()
    # The oldest adopted databases never had the column; with no format stored
    # the new default already applies to them.
    if "tts_format" not in {column["name"] for column in sa.inspect(bind).get_columns("app_settings")}:
        return
    bind.execute(
        sa.text("UPDATE app_settings SET tts_format = :new WHERE lower(tts_format) = :old"),
        {"new": NEW_DEFAULT, "old": OLD_DEFAULT},
    )


def downgrade() -> None:
    # Which accounts were moved is not recorded, and guessing would overwrite
    # MP3 that somebody chose. The change is harmless to keep.
    pass
