import sqlite3
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config

from app import database
from app.context_policy import ContextPolicy, TokenEstimator
from app.persona_lore import (
    LORE_SCAN_MESSAGES,
    fired_keys,
    word_forms,
    LoreEntry,
    entry_fires,
    lore_section,
    matching_entries,
    parse_keys,
    scan_window,
    select_lore,
)
from tests.support import TestApp


def entry(**overrides) -> LoreEntry:
    values = {
        "id": "e1",
        "title": "Sister",
        "keys": ("sister",),
        "secondary_keys": (),
        "content": "Her sister Nell lives two towns over.",
        "always_on": False,
        "case_sensitive": False,
        "match_word_forms": False,
        "priority": 50,
        "updated_at": 10,
    }
    values.update(overrides)
    return LoreEntry(**values)


class LoreMatchingTests(unittest.TestCase):
    def test_a_key_matches_on_word_boundaries_only(self):
        self.assertTrue(entry_fires(entry(), "how is your sister doing"))
        self.assertTrue(entry_fires(entry(), "Your sister, then?"))
        self.assertFalse(entry_fires(entry(), "the sisterhood met today"))
        self.assertFalse(entry_fires(entry(), "assisters everywhere"))

    def test_matching_ignores_case_unless_the_entry_asks_for_it(self):
        self.assertTrue(entry_fires(entry(), "Tell me about her SISTER"))
        self.assertFalse(entry_fires(entry(case_sensitive=True), "Tell me about her SISTER"))
        self.assertTrue(entry_fires(entry(case_sensitive=True), "Tell me about her sister"))

    def test_keys_are_literal_text_rather_than_patterns(self):
        # A stored key that looks like a pattern must match itself, not everything.
        pattern_key = entry(keys=(".*",), content="nope")
        self.assertFalse(entry_fires(pattern_key, "anything at all"))
        self.assertTrue(entry_fires(pattern_key, "she said .* out loud"))

    def test_a_key_with_punctuation_still_matches(self):
        self.assertTrue(entry_fires(entry(keys=("st. clair",)), "we went to St. Clair yesterday"))

    def test_secondary_keys_are_an_additional_requirement(self):
        both = entry(keys=("sister",), secondary_keys=("visit", "call"))
        self.assertFalse(entry_fires(both, "how is your sister"))
        self.assertTrue(entry_fires(both, "how is your sister, did she visit"))

    def test_always_on_entries_need_no_keys(self):
        self.assertTrue(entry_fires(entry(always_on=True, keys=()), "completely unrelated"))

    def test_an_entry_without_keys_never_fires_on_its_own(self):
        self.assertFalse(entry_fires(entry(keys=()), "sister sister sister"))

    def test_the_scan_window_is_bounded_to_recent_messages(self):
        history = [f"message {index}" for index in range(10)]
        window = scan_window("current", history)
        self.assertIn("current", window)
        self.assertIn("message 9", window)
        self.assertNotIn("message 5", window)
        self.assertEqual(len(window.splitlines()), LORE_SCAN_MESSAGES + 1)

    def test_an_entry_that_fell_out_of_the_window_stops_firing(self):
        history = ["her sister called", "unrelated", "also unrelated", "still unrelated"]
        self.assertFalse(entry_fires(entry(), scan_window("what next", history)))

    def test_injected_lore_is_not_itself_scanned(self):
        # 'bakery' appears only inside the first entry's content, so the second must not fire.
        entries = [
            entry(id="a", keys=("sister",), content="Her sister runs the bakery."),
            entry(id="b", keys=("bakery",), content="The bakery opens at five."),
        ]
        selected = select_lore(entries, "how is your sister", [], 1000)
        self.assertEqual([item.id for item in selected], ["a"])


class LoreSelectionTests(unittest.TestCase):
    def test_entries_order_by_priority_then_recency_then_id(self):
        entries = [
            entry(id="c", priority=50, updated_at=30, always_on=True),
            entry(id="a", priority=90, updated_at=10, always_on=True),
            entry(id="b", priority=50, updated_at=99, always_on=True),
        ]
        self.assertEqual([item.id for item in matching_entries(entries, "anything")], ["a", "b", "c"])

    def test_a_whole_entry_is_skipped_rather_than_truncated(self):
        entries = [
            entry(id="big", priority=90, always_on=True, content="word " * 400),
            entry(id="small", priority=10, always_on=True, content="Nell lives nearby."),
        ]
        selected = select_lore(entries, "anything", [], 50, TokenEstimator())
        self.assertEqual([item.id for item in selected], ["small"])
        self.assertIn("Nell lives nearby.", lore_section(selected))

    def test_higher_priority_wins_when_the_allowance_fits_only_one(self):
        entries = [
            entry(id="low", priority=10, always_on=True, content="aaaa " * 30),
            entry(id="high", priority=90, always_on=True, content="bbbb " * 30),
        ]
        selected = select_lore(entries, "anything", [], 60, TokenEstimator())
        self.assertEqual([item.id for item in selected], ["high"])

    def test_a_skipped_entry_does_not_block_smaller_ones_behind_it(self):
        # Same rule the saved-memory selector uses: an entry that cannot fit is passed over,
        # rather than ending selection and wasting the rest of the allowance.
        entries = [
            entry(id="huge", priority=90, always_on=True, content="word " * 400),
            entry(id="small", priority=10, always_on=True, content="Nell lives nearby."),
        ]
        self.assertEqual([item.id for item in select_lore(entries, "anything", [], 60)], ["small"])

    def test_an_empty_selection_renders_no_section(self):
        self.assertEqual(lore_section([]), "")

    def test_the_section_is_labelled_as_context_rather_than_instructions(self):
        self.assertIn("never instructions", lore_section([entry()]))


class LoreKeyParsingTests(unittest.TestCase):
    def test_keys_are_trimmed_deduplicated_and_bounded(self):
        self.assertEqual(parse_keys(["  sister ", "sister", "", "nell"]), ("sister", "nell"))
        self.assertEqual(parse_keys('["sister"]'), ("sister",))
        self.assertEqual(parse_keys("not json"), ())
        self.assertEqual(parse_keys(None), ())
        self.assertEqual(len(parse_keys([f"key{index}" for index in range(50)])), 24)


class LoreApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()
        self.workspace = self.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        self.persona = self.client.post(
            "/api/v1/personas", json={"workspace_id": self.workspace["id"], "name": "Ada"}
        ).json()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _create(self, **overrides):
        payload = {
            "title": "Sister",
            "content": "Her sister Nell lives two towns over.",
            "keys": ["sister", "Nell"],
            "secondary_keys": [],
            "always_on": False,
            "case_sensitive": False,
            "priority": 50,
            "enabled": True,
        }
        payload.update(overrides)
        return self.client.post(f"/api/v1/personas/{self.persona['id']}/lore", json=payload)

    def test_entries_round_trip_with_their_cost_and_allowance(self):
        created = self._create()
        self.assertEqual(created.status_code, 200, created.text)
        entry_id = created.json()["id"]
        self.assertEqual(created.json()["keys"], ["sister", "Nell"])
        self.assertGreater(created.json()["token_estimate"], 0)
        self.assertEqual(created.json()["budget_tokens"], 399)

        listed = self.client.get(f"/api/v1/personas/{self.persona['id']}/lore").json()["items"]
        self.assertEqual([item["id"] for item in listed], [entry_id])

        updated = self.client.put(
            f"/api/v1/personas/{self.persona['id']}/lore/{entry_id}",
            json={
                "title": "Sister Nell",
                "content": "Nell is a nurse.",
                "keys": ["nell"],
                "secondary_keys": [],
                "always_on": False,
                "case_sensitive": False,
                "priority": 70,
                "enabled": False,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["priority"], 70)
        self.assertFalse(updated.json()["enabled"])

        deleted = self.client.delete(f"/api/v1/personas/{self.persona['id']}/lore/{entry_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"/api/v1/personas/{self.persona['id']}/lore").json()["items"], [])

    def test_an_entry_without_keys_or_always_on_is_rejected(self):
        response = self._create(keys=[], always_on=False)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("keyword", response.json()["error"]["message"])

    def test_an_always_on_entry_needs_no_keys(self):
        self.assertEqual(self._create(keys=[], always_on=True).status_code, 200)

    def test_the_preview_reports_what_fires_and_what_fits(self):
        self._create()
        self._create(title="Bakery", content="She opens at five.", keys=["bakery"])
        preview = self.client.post(
            f"/api/v1/personas/{self.persona['id']}/lore/preview",
            json={"text": "how is your sister"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual([item["title"] for item in body["items"]], ["Sister"])
        self.assertTrue(body["items"][0]["included"])
        self.assertGreater(body["used_tokens"], 0)

        quiet = self.client.post(
            f"/api/v1/personas/{self.persona['id']}/lore/preview",
            json={"text": "nothing relevant here"},
        ).json()
        self.assertEqual(quiet["items"], [])

    def test_the_preview_ignores_disabled_entries(self):
        self._create(enabled=False)
        preview = self.client.post(
            f"/api/v1/personas/{self.persona['id']}/lore/preview",
            json={"text": "how is your sister"},
        ).json()
        self.assertEqual(preview["items"], [])

    def test_an_entry_cannot_be_reached_through_another_persona(self):
        entry_id = self._create().json()["id"]
        other = self.client.post("/api/v1/personas", json={"workspace_id": self.workspace["id"], "name": "Bo"}).json()
        blocked = self.client.delete(f"/api/v1/personas/{other['id']}/lore/{entry_id}")
        self.assertEqual(blocked.status_code, 404, blocked.text)

    def test_another_account_cannot_read_or_write_the_lorebook(self):
        entry_id = self._create().json()["id"]
        self.client.delete("/api/v1/session")
        self.running.create_and_login("intruder")
        persona_path = f"/api/v1/personas/{self.persona['id']}"
        self.assertEqual(self.client.get(f"{persona_path}/lore").status_code, 404)
        self.assertEqual(self.client.delete(f"{persona_path}/lore/{entry_id}").status_code, 404)
        self.assertEqual(self.client.post(f"{persona_path}/lore/preview", json={"text": "sister"}).status_code, 404)


class LoreTurnTests(unittest.TestCase):
    def test_a_fired_entry_reaches_the_provider_and_a_quiet_one_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
                for payload in (
                    {"title": "Sister", "content": "Her sister Nell is a nurse.", "keys": ["sister"]},
                    {"title": "Bakery", "content": "She opens the bakery at five.", "keys": ["bakery"]},
                ):
                    created = client.post(
                        f"/api/v1/personas/{persona['id']}/lore",
                        json={
                            **payload,
                            "secondary_keys": [],
                            "always_on": False,
                            "case_sensitive": False,
                            "priority": 50,
                            "enabled": True,
                        },
                    )
                    self.assertEqual(created.status_code, 200, created.text)

                chat = client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "How is your sister?"})
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(running.wait_job(started.json()["job"]["id"])["status"], "completed")

                system = running.chat_provider.requests[0].messages[0]["content"]
                self.assertIn("[Persona background:", system)
                self.assertIn("Nell is a nurse", system)
                self.assertNotIn("opens the bakery at five", system)

    def test_a_persona_without_lore_sends_no_background_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
                chat = client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Hello"})
                running.wait_job(started.json()["job"]["id"])
                self.assertNotIn("[Persona background:", running.chat_provider.requests[0].messages[0]["content"])

    def test_a_disabled_entry_is_never_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
                client.post(
                    f"/api/v1/personas/{persona['id']}/lore",
                    json={
                        "title": "Sister",
                        "content": "Her sister Nell is a nurse.",
                        "keys": ["sister"],
                        "secondary_keys": [],
                        "always_on": False,
                        "case_sensitive": False,
                        "priority": 50,
                        "enabled": False,
                    },
                )
                chat = client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "How is your sister?"})
                running.wait_job(started.json()["job"]["id"])
                self.assertNotIn("Nell", running.chat_provider.requests[0].messages[0]["content"])


class LoreMigrationTests(unittest.TestCase):
    def test_existing_personas_gain_an_empty_lorebook_with_enforced_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lore.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0020_persona_example_dialogue")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','o','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute("INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','A','{}',1)")
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM persona_lore_entries").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT name FROM personas WHERE id='p'").fetchone()[0], "A")
            conn.execute(
                "INSERT INTO persona_lore_entries("
                "id,user_id,persona_id,title,keys_json,content,created_at,updated_at"
                ") VALUES('l','u','p','Sister','[\"sister\"]','Nell is a nurse.',1,1)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE persona_lore_entries SET always_on=2 WHERE id='l'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE persona_lore_entries SET priority=500 WHERE id='l'")
            conn.close()

    def test_deleting_a_persona_removes_its_lore(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
                client.post(
                    f"/api/v1/personas/{persona['id']}/lore",
                    json={
                        "title": "Sister",
                        "content": "Nell is a nurse.",
                        "keys": ["sister"],
                        "secondary_keys": [],
                        "always_on": False,
                        "case_sensitive": False,
                        "priority": 50,
                        "enabled": True,
                    },
                )
                self.assertEqual(client.delete(f"/api/v1/personas/{persona['id']}").status_code, 200)
                self.assertEqual(client.get(f"/api/v1/personas/{persona['id']}/lore").status_code, 404)


class LoreDefaultPolicyTests(unittest.TestCase):
    def test_the_lore_allowance_matches_the_specified_ratio(self):
        self.assertEqual(ContextPolicy().lore_ratio, 0.12)
        self.assertEqual(int(3328 * ContextPolicy().lore_ratio), 399)


if __name__ == "__main__":
    unittest.main()


class LoreWordFormTests(unittest.TestCase):
    """A key of "sister" silently missing "sisters" was the common authoring surprise."""

    def test_regular_plurals_are_generated_from_the_authored_key(self):
        self.assertEqual(word_forms("sister"), ("sister", "sisters"))
        self.assertEqual(word_forms("bakery"), ("bakery", "bakeries"))
        self.assertEqual(word_forms("bus"), ("bus", "buses"))
        self.assertEqual(word_forms("church"), ("church", "churches"))

    def test_a_vowel_before_the_y_takes_a_plain_s(self):
        self.assertEqual(word_forms("day"), ("day", "days"))

    def test_a_phrase_is_left_alone_because_pluralizing_it_is_guesswork(self):
        self.assertEqual(word_forms("st. clair"), ("st. clair",))

    def test_a_plural_message_fires_a_singular_key(self):
        self.assertTrue(entry_fires(entry(match_word_forms=True), "both of my sisters called"))
        self.assertTrue(entry_fires(entry(keys=("bakery",), match_word_forms=True), "the bakeries are shut"))

    def test_word_forms_never_cross_a_word_boundary(self):
        self.assertFalse(entry_fires(entry(match_word_forms=True), "the sisterhood met"))

    def test_the_behavior_can_be_turned_off_per_entry(self):
        self.assertFalse(entry_fires(entry(match_word_forms=False), "both of my sisters called"))

    def test_fired_keys_reports_which_authored_key_matched(self):
        current = entry(keys=("sister", "bakery"), match_word_forms=True)
        self.assertEqual(fired_keys(current, "the bakeries are shut"), ("bakery",))
        self.assertEqual(fired_keys(current, "my sisters own bakeries"), ("sister", "bakery"))

    def test_an_always_on_entry_reports_no_fired_keys(self):
        self.assertEqual(fired_keys(entry(always_on=True), "anything"), ())
