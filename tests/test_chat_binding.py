"""A chat is bound to one workspace and persona, at creation, for good.

Retargeting a chat that already has a transcript left the previous persona's
replies in the next model prompt, and let a persona from another workspace be
saved onto a chat that would then fail on its next turn. Both are reproduced
here before they are refused. See ADR 0032.
"""

from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config

from app import database
from tests.support import FakeChatProvider, TestApp


class ChatBindingTests(unittest.TestCase):
    def _world(self, running):
        """Two workspaces, a persona in each. The shape both defects need."""

        running.create_and_login()
        home = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        work = running.client.post("/api/v1/workspaces", json={"name": "Work"}).json()
        avery = running.client.post(
            "/api/v1/personas",
            json={"workspace_id": home["id"], "workspace_ids": [home["id"]], "name": "Avery"},
        ).json()
        blake = running.client.post(
            "/api/v1/personas",
            json={"workspace_id": work["id"], "workspace_ids": [work["id"]], "name": "Blake"},
        ).json()
        return home, work, avery, blake

    def _chat(self, running, workspace, persona):
        return running.client.post(
            "/api/v1/chats",
            json={
                "workspace_id": workspace["id"],
                "persona_id": persona["id"],
                "title": "Chat",
                "memory_mode": "off",
            },
        ).json()

    def _turn(self, running, chat_id: str, text: str, **extra):
        payload = {"text": text, "memory_mode": "off"}
        payload.update(extra)
        return running.client.post(f"/api/v1/chats/{chat_id}/turns", json=payload)

    # -- reproduction one: saving a persona from another workspace ---------

    def test_a_cross_workspace_persona_is_refused_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            home, _work, avery, blake = self._world(running)
            chat = self._chat(running, home, avery)

            refused = running.client.put(f"/api/v1/chats/{chat['id']}", json={"persona_id": blake["id"]})

            self.assertEqual(refused.status_code, 409, refused.text)
            stored = running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]
            self.assertEqual(stored["persona_id"], avery["id"])
            self.assertEqual(stored["workspace_id"], home["id"])

    def test_even_a_persona_in_the_same_workspace_cannot_replace_another(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            home, _work, avery, _blake = self._world(running)
            second = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": home["id"], "workspace_ids": [home["id"]], "name": "Casey"},
            ).json()
            chat = self._chat(running, home, avery)

            # Valid persona, valid workspace, still refused: the transcript was
            # written by Avery and would otherwise be handed to Casey.
            refused = running.client.put(f"/api/v1/chats/{chat['id']}", json={"persona_id": second["id"]})

            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(
                running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["persona_id"],
                avery["id"],
            )

    def test_the_rest_of_a_chat_is_still_editable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            home, _work, avery, _blake = self._world(running)
            chat = self._chat(running, home, avery)

            updated = running.client.put(
                f"/api/v1/chats/{chat['id']}",
                json={"title": "Renamed", "memory_mode": "saved", "persona_id": avery["id"]},
            )

            # Repeating the persona it is already bound to is not a change.
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["title"], "Renamed")
            self.assertEqual(updated.json()["memory_mode"], "saved")

    # -- reproduction two: a turn retargeting its own chat -----------------

    def test_a_turn_cannot_move_its_chat_to_another_persona(self):
        provider = FakeChatProvider(["Fine."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            home, _work, avery, blake = self._world(running)
            chat = self._chat(running, home, avery)
            running.wait_job(self._turn(running, chat["id"], "hello").json()["job"]["id"])

            refused = self._turn(running, chat["id"], "and now?", persona_id=blake["id"])

            self.assertEqual(refused.status_code, 409, refused.text)
            stored = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(stored["chat"]["persona_id"], avery["id"])
            # Refused before anything durable: the second message was never
            # written, so the transcript is exactly what it was.
            self.assertEqual([message["role"] for message in stored["messages"]], ["user", "assistant"])

    def test_a_turn_cannot_move_its_chat_to_another_workspace(self):
        provider = FakeChatProvider(["Fine."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            home, work, avery, _blake = self._world(running)
            chat = self._chat(running, home, avery)

            refused = self._turn(running, chat["id"], "hello", workspace_id=work["id"])

            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"], [])

    def test_repeating_the_bound_values_still_works(self):
        provider = FakeChatProvider(["Fine."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            home, _work, avery, _blake = self._world(running)
            chat = self._chat(running, home, avery)

            # The published API still accepts both fields; an older client that
            # repeats what the chat already says must keep working.
            accepted = self._turn(
                running,
                chat["id"],
                "hello",
                persona_id=avery["id"],
                workspace_id=home["id"],
            )

            self.assertEqual(accepted.status_code, 202, accepted.text)
            running.wait_job(accepted.json()["job"]["id"])

    def test_a_turn_that_names_nothing_uses_what_the_chat_is_bound_to(self):
        provider = FakeChatProvider(["Fine."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            home, _work, avery, _blake = self._world(running)
            chat = self._chat(running, home, avery)

            accepted = self._turn(running, chat["id"], "hello")
            self.assertEqual(accepted.status_code, 202, accepted.text)
            running.wait_job(accepted.json()["job"]["id"])

            stored = running.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]
            self.assertEqual(stored["persona_id"], avery["id"])
            self.assertEqual(stored["workspace_id"], home["id"])

    # -- creation is still where the binding is decided --------------------

    def test_a_chat_cannot_be_created_with_a_persona_from_another_workspace(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            home, _work, _avery, blake = self._world(running)

            refused = running.client.post(
                "/api/v1/chats",
                json={"workspace_id": home["id"], "persona_id": blake["id"], "title": "Chat"},
            )

            self.assertEqual(refused.status_code, 404, refused.text)

    def test_a_chat_created_with_only_a_persona_adopts_its_workspace(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            home, _work, avery, _blake = self._world(running)

            created = running.client.post("/api/v1/chats", json={"persona_id": avery["id"], "title": "Chat"}).json()

            self.assertEqual(created["workspace_id"], home["id"])


class ChatBindingRepairTests(unittest.TestCase):
    """Rows that predate the invariant are repaired, never discarded."""

    def _legacy(self, path: Path) -> None:
        """A database one revision before the repair, holding the bad rows.

        Built by upgrading to `0030` and inserting directly, because a database
        already at head would simply not run the migration under test.
        """

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
        engine = database.build_engine(path)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0030_scene_production_link")
        engine.dispose()
        conn = database.connect_sqlite(path)
        conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
        conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('home','u','Home',1)")
        conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('work','u','Work',1)")
        conn.execute("INSERT INTO personas(id,workspace_id,name,created_at) VALUES('avery','home','Avery',1)")
        conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('avery','home')")
        conn.commit()
        conn.close()

    def _upgrade(self, path: Path) -> tuple:
        database.upgrade_database(path)
        conn = database.connect_sqlite(path)
        row = conn.execute("SELECT workspace_id, persona_id FROM chats WHERE id='c'").fetchone()
        messages = [item[0] for item in conn.execute("SELECT role FROM messages WHERE chat_id='c'").fetchall()]
        conn.commit()
        conn.close()
        return row[0], row[1], messages

    def test_a_persona_outside_its_chat_workspace_keeps_the_persona(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            self._legacy(path)
            conn = database.connect_sqlite(path)
            # What a retargeting turn used to leave behind.
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','work','avery','Chat',1,1)"
            )
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('m','c','user','hello',1)")
            conn.commit()
            conn.close()

            workspace_id, persona_id, messages = self._upgrade(path)

            # The persona wrote the transcript, so the persona is what is kept
            # and the workspace is corrected to one it belongs to.
            self.assertEqual(persona_id, "avery")
            self.assertEqual(workspace_id, "home")
            # Nothing deleted, nothing reattributed.
            self.assertEqual(messages, ["user"])

    def test_a_persona_with_no_workspace_adopts_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            self._legacy(path)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u',NULL,'avery','Chat',1,1)"
            )
            conn.commit()
            conn.close()

            workspace_id, persona_id, _messages = self._upgrade(path)

            self.assertEqual((workspace_id, persona_id), ("home", "avery"))

    def test_a_consistent_chat_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            self._legacy(path)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','home','avery','Chat',1,1)"
            )
            conn.commit()
            conn.close()

            self.assertEqual(self._upgrade(path)[:2], ("home", "avery"))

    def test_a_chat_with_no_persona_is_never_given_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            self._legacy(path)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','work',NULL,'Chat',1,1)"
            )
            conn.commit()
            conn.close()

            # A chat nobody was speaking as stays exactly as it is.
            self.assertEqual(self._upgrade(path)[:2], ("work", None))


if __name__ == "__main__":
    unittest.main()
