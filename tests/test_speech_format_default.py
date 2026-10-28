"""Replies are spoken in a format that can start before it is finished.

The default was WAV, which streaming speech (ADR 0037) and speaking while the
reply is written (ADR 0042) both have to skip: its length lives in a header
nothing can fill in halfway through. Every account that never changed the
format waited for the whole reply to be synthesized and was never told why.
"""

import importlib.util
from pathlib import Path
import re
import unittest

import sqlalchemy as sa

from app.speech_clients import DEFAULT_TTS_FORMAT, STREAMABLE_FORMATS


ROOT = Path(__file__).resolve().parents[1]


class DefaultTests(unittest.TestCase):
    def test_the_default_can_start_playing_before_it_is_finished(self):
        self.assertIn(DEFAULT_TTS_FORMAT, STREAMABLE_FORMATS)

    def test_the_browser_agrees(self):
        # TypeScript cannot import the Python constant, so its copy is checked
        # here and drift fails the build rather than the conversation.
        source = (ROOT / "frontend" / "src" / "settings.ts").read_text(encoding="utf-8")
        match = re.search(r"^\s*tts_format:\s*'([^']*)',", source, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), DEFAULT_TTS_FORMAT)

    def test_no_other_default_is_written_down(self):
        # One definition. A second literal is how the old default lived on in
        # six places after it should have changed.
        for path in (ROOT / "app").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"""tts_format["']?\]?\s*(or|default=|:)\s*["']wav["']""", path.name)


def _migration():
    path = ROOT / "migrations" / "versions" / "0042_streamable_default_speech_format.py"
    spec = importlib.util.spec_from_file_location("revision_0042", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationTests(unittest.TestCase):
    def _migrated(self, rows: dict[str, str]) -> dict[str, str]:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE app_settings (user_id TEXT PRIMARY KEY, tts_format TEXT)"))
            for user_id, value in rows.items():
                connection.execute(
                    sa.text("INSERT INTO app_settings VALUES (:user_id, :value)"), {"user_id": user_id, "value": value}
                )
            context = MigrationContext.configure(connection)
            with Operations.context(Operations(context)):
                _migration().upgrade()
            return dict(connection.execute(sa.text("SELECT user_id, tts_format FROM app_settings")).fetchall())

    def test_an_account_on_wav_moves_to_mp3(self):
        self.assertEqual(self._migrated({"owner": "wav", "shouty": "WAV"}), {"owner": "mp3", "shouty": "mp3"})

    def test_every_other_format_is_left_alone(self):
        migrated = self._migrated({"a": "aac", "b": "opus", "c": "flac", "d": "mp3"})
        self.assertEqual(migrated, {"a": "aac", "b": "opus", "c": "flac", "d": "mp3"})


if __name__ == "__main__":
    unittest.main()
