"""A persona's voice settings, keyed by provider rather than by column.

A persona carried nine text-to-speech columns, six with a provider name in them.
Adding a third provider meant three more columns and a migration, and a provider
nobody had added a column for resolved silently to nothing. One keyed object
replaces them, and the unqualified trio became a `default` any provider falls
back to.
"""

from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from alembic import command
from alembic.config import Config

from app import database
from app.persona_voice import dump, normalize, parse, preference
from tests.support import TestApp


class VoicePreferenceRecordTests(unittest.TestCase):
    def test_a_provider_gets_what_it_asked_for(self):
        values = {"openai": {"voice": "marin"}, "local": {"voice": "af_heart"}}

        self.assertEqual(preference(values, "openai", "voice"), "marin")
        self.assertEqual(preference(values, "local", "voice"), "af_heart")

    def test_an_unknown_provider_falls_back_to_the_default(self):
        values = {"default": {"voice": "marin"}, "local": {"voice": "af_heart"}}

        # The whole point: a provider this deployment adds later is honoured
        # without a schema change and without a column named after it.
        self.assertEqual(preference(values, "elevenlabs", "voice"), "marin")

    def test_a_provider_entry_beats_the_default(self):
        values = {"default": {"voice": "marin"}, "local": {"voice": "af_heart"}}

        self.assertEqual(preference(values, "local", "voice"), "af_heart")

    def test_nothing_stored_is_nothing_claimed(self):
        self.assertEqual(preference({}, "openai", "voice"), "")
        self.assertEqual(preference({"openai": {}}, "openai", "voice"), "")

    def test_an_empty_entry_is_not_stored_at_all(self):
        # "Has an opinion about this provider" and "was opened and left blank"
        # must not look the same later.
        self.assertEqual(normalize({"openai": {"voice": "  "}}), {})

    def test_unknown_fields_are_dropped_rather_than_stored(self):
        self.assertEqual(normalize({"openai": {"voice": "marin", "pitch": "high"}}), {"openai": {"voice": "marin"}})

    def test_a_malformed_record_reads_as_empty(self):
        self.assertEqual(parse("not json"), {})
        self.assertEqual(parse(None), {})

    def test_it_round_trips(self):
        values = {"openai": {"voice": "marin", "model": "gpt-4o-mini-tts", "speed": "1.1"}}

        self.assertEqual(parse(dump(values)), values)


class VoicePreferenceMigrationTests(unittest.TestCase):
    def test_existing_columns_become_one_keyed_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0034_preset_signals")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','o','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','Home',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,created_at,preferred_voice,preferred_tts_speed,"
                "preferred_voice_local,preferred_tts_model_local) "
                "VALUES('p','w','Avery',1,'marin','1.2','af_heart','kokoro')"
            )
            conn.commit()
            conn.close()

            database.upgrade_database(path)

            conn = sqlite3.connect(path)
            stored = json.loads(conn.execute("SELECT voice_preferences_json FROM personas WHERE id='p'").fetchone()[0])
            columns = {row[1] for row in conn.execute("PRAGMA table_info(personas)")}
            conn.close()

            # The unqualified trio was set under whichever provider was
            # configured then, so it becomes the fallback rather than being
            # assigned to one.
            self.assertEqual(stored["default"], {"voice": "marin", "speed": "1.2"})
            self.assertEqual(stored["local"], {"voice": "af_heart", "model": "kokoro"})
            self.assertNotIn("openai", stored)
            # Leaving nine unread columns would preserve the exact shape this
            # migration exists to remove.
            self.assertFalse({column for column in columns if column.startswith("preferred_")})
            self.assertIn("voice_preferences_json", columns)


class VoicePreferenceEndpointTests(unittest.TestCase):
    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post(
            "/api/v1/personas",
            json={"workspace_id": workspace["id"], "name": "Avery"},
        ).json()

    def test_a_persona_stores_and_returns_its_voice_preferences(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            self.assertEqual(persona["voice_preferences"], {})

            updated = running.client.put(
                f"/api/v1/personas/{persona['id']}",
                json={
                    "workspace_id": persona["workspace_id"],
                    "name": "Avery",
                    "voice_preferences": {"local": {"voice": "af_heart", "speed": "1.1"}},
                },
            )

            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["voice_preferences"], {"local": {"voice": "af_heart", "speed": "1.1"}})

    def test_a_provider_with_no_entry_falls_back_rather_than_failing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            running.client.put(
                f"/api/v1/personas/{persona['id']}",
                json={
                    "workspace_id": persona["workspace_id"],
                    "name": "Avery",
                    "voice_preferences": {"default": {"voice": "marin"}},
                },
            )

            stored = running.client.get(f"/api/v1/personas/{persona['id']}").json()["voice_preferences"]

            self.assertEqual(preference(stored, "a-provider-added-next-year", "voice"), "marin")


if __name__ == "__main__":
    unittest.main()
