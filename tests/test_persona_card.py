import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config

from app import database
from app.chat import persona_instruction_block
from app.context_policy import ContextPolicy, TokenEstimator
from app.context_service import ContextService
from app.persona_card import (
    CARD_FIELDS,
    card_budget,
    card_token_estimate,
    example_dialogue_blocks,
    example_dialogue_fit,
    render_card_block,
    render_example_block,
    select_example_dialogue,
)
from tests.support import TestApp


# Priced by both this module and frontend/tests/persona_card.test.ts. If the labels or the
# estimator drift apart, the browser stops showing what the server will actually enforce,
# and one of the two assertions fails.
SHARED_CARD_FIXTURE = {
    "card_definition": "Runs a neighbourhood bakery and lives above it.",
    "card_personality": "Warm, stubborn, quietly afraid of being left behind.",
    "card_style": "Short sentences. Trails off mid-thought when tired.",
    "card_behavior": "Asks a follow-up before giving advice.",
}
SHARED_CARD_TOKENS = 131


class PersonaCardRenderingTests(unittest.TestCase):
    def test_card_renders_only_populated_fields_in_a_fixed_order(self):
        rendered = render_card_block(
            {"card_definition": "Bakes bread.", "card_personality": "", "card_behavior": "Asks questions."}
        )
        self.assertEqual(
            rendered.splitlines(),
            [
                "Character definition (facts about who this persona is): Bakes bread.",
                "Character behavior (how this persona acts): Asks questions.",
            ],
        )

    def test_empty_card_costs_nothing_and_leaves_the_prompt_unchanged(self):
        empty = {field: "" for field in CARD_FIELDS}
        self.assertEqual(render_card_block(empty), "")
        self.assertEqual(card_token_estimate(empty), 0)
        row = {"name": "Ada", "traits_json": "{}", "personality_details": None, "system_prompt": "Be brief.", **empty}
        self.assertNotIn("Character definition", persona_instruction_block(row))

    def test_card_outranks_details_and_the_system_prompt_stays_last(self):
        row = {
            "name": "Ada",
            "traits_json": "{}",
            "personality_details": "Likes lists.",
            "system_prompt": "Answer briefly.",
            **SHARED_CARD_FIXTURE,
        }
        lines = persona_instruction_block(row).splitlines()
        card_line = next(index for index, line in enumerate(lines) if line.startswith("Character definition"))
        details_line = lines.index("Persona details: Likes lists.")
        self.assertLess(card_line, details_line)
        self.assertEqual(lines[-1], "Answer briefly.")

    def test_shared_fixture_price_matches_the_browser_estimate(self):
        self.assertEqual(card_token_estimate(SHARED_CARD_FIXTURE), SHARED_CARD_TOKENS)


class PersonaCardBudgetTests(unittest.TestCase):
    def test_budget_uses_the_same_reserves_as_turn_planning(self):
        budget = card_budget({}, ContextPolicy())
        self.assertEqual(budget.context_window_tokens, 4096)
        self.assertEqual(budget.prompt_budget_tokens, 3328)
        self.assertEqual(budget.cap_tokens, 998)

    def test_raising_the_context_allocation_raises_the_cap(self):
        budget = card_budget({"models_context_window_tokens": 8192}, ContextPolicy())
        self.assertEqual(budget.prompt_budget_tokens, 7270)
        self.assertEqual(budget.cap_tokens, 2181)

    def test_cap_follows_the_narrowest_configured_model_window(self):
        preferences = {
            "models_context_window_tokens": 8192,
            "model_overrides": {"small": {"context_window_tokens": 4096}, "big": {"context_window_tokens": 16384}},
        }
        self.assertEqual(card_budget(preferences, ContextPolicy()).context_window_tokens, 4096)

    def test_unusable_window_settings_fall_back_to_the_supported_minimum(self):
        budget = card_budget({"models_context_window_tokens": 64}, ContextPolicy())
        self.assertEqual(budget.context_window_tokens, 2048)


class ExampleDialogueTests(unittest.TestCase):
    RAW = (
        "<START>\n"
        "{{user}}: You up?\n"
        "{{char}}: Barely. Three episodes into something I don't even like.\n"
        "<START>\n"
        "{{user}}: I got the job.\n"
        "{{char}}: Shut up. Okay, start from the beginning.\n"
    )

    def test_blocks_split_on_the_delimiter_and_ignore_empty_sections(self):
        blocks = example_dialogue_blocks("<START>\n\n<START>\nfirst\n<START>\n   \n<START>\nsecond")
        self.assertEqual(blocks, ["first", "second"])

    def test_placeholders_substitute_at_render(self):
        rendered = render_example_block("{{user}}: hi\n{{char}}: hello", "Ada")
        self.assertEqual(rendered, "User: hi\nAda: hello")

    def test_an_unnamed_persona_still_renders_a_usable_speaker(self):
        self.assertEqual(render_example_block("{{char}}: hello", ""), "Assistant: hello")

    def test_whole_exchanges_are_included_and_later_ones_drop_first(self):
        estimator = TokenEstimator()
        everything = select_example_dialogue(self.RAW, "Ada", 1000, estimator)
        self.assertIn("You up?", everything)
        self.assertIn("I got the job.", everything)

        first_only = select_example_dialogue(self.RAW, "Ada", 40, estimator)
        self.assertIn("You up?", first_only)
        self.assertNotIn("I got the job.", first_only)
        self.assertNotIn("{{char}}", first_only)

    def test_a_single_oversized_exchange_is_omitted_rather_than_truncated(self):
        raw = "<START>\n{{user}}: hi\n{{char}}: " + ("word " * 400)
        self.assertEqual(select_example_dialogue(raw, "Ada", 40, TokenEstimator()), "")

    def test_fit_reports_authored_and_included_counts(self):
        budget = card_budget({}, ContextPolicy())
        authored, included, cost = example_dialogue_fit(self.RAW, "Ada", budget)
        self.assertEqual((authored, included), (2, 2))
        self.assertGreater(cost, 0)
        self.assertEqual(example_dialogue_fit(None, "Ada", budget), (0, 0, 0))


class PersonaCardMigrationTests(unittest.TestCase):
    def test_existing_personas_survive_with_an_empty_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona-card.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0018_human_image_delivery")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,personality_details,system_prompt,created_at) "
                "VALUES('p','w','Avery','{\"warmth\": 70}','Likes lists.','Answer briefly.',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            row = conn.execute(
                "SELECT personality_details,system_prompt,card_definition,card_personality,card_style,"
                "card_behavior,card_token_estimate FROM personas WHERE id='p'"
            ).fetchone()
            conn.close()
            self.assertEqual(tuple(row), ("Likes lists.", "Answer briefly.", None, None, None, None, 0))
            migrated = {
                "name": "Avery",
                "traits_json": '{"warmth": 70}',
                "personality_details": row[0],
                "system_prompt": row[1],
                **{field: None for field in CARD_FIELDS},
            }
            self.assertNotIn("Character definition", persona_instruction_block(migrated))


class PersonaCardApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()
        self.workspace = self.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        self.persona = self.client.post(
            "/api/v1/personas",
            json={"workspace_id": self.workspace["id"], "name": "Ada"},
        ).json()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_new_persona_starts_with_an_empty_card_and_a_stated_budget(self):
        self.assertIsNone(self.persona["card_definition"])
        self.assertEqual(self.persona["card_token_estimate"], 0)
        self.assertEqual(self.persona["card_cap_tokens"], 998)
        self.assertEqual(self.persona["card_prompt_budget_tokens"], 3328)

    def test_saving_a_card_stores_it_and_reports_its_cost(self):
        response = self.client.put(f"/api/v1/personas/{self.persona['id']}/card", json=SHARED_CARD_FIXTURE)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["card_style"], SHARED_CARD_FIXTURE["card_style"])
        self.assertEqual(saved["card_token_estimate"], SHARED_CARD_TOKENS)
        listed = self.client.get("/api/v1/personas").json()["items"][0]
        self.assertEqual(listed["card_definition"], SHARED_CARD_FIXTURE["card_definition"])

    def test_clearing_a_field_clears_its_cost(self):
        self.client.put(f"/api/v1/personas/{self.persona['id']}/card", json=SHARED_CARD_FIXTURE)
        cleared = self.client.put(
            f"/api/v1/personas/{self.persona['id']}/card",
            json={field: "" for field in CARD_FIELDS},
        ).json()
        self.assertIsNone(cleared["card_definition"])
        self.assertEqual(cleared["card_token_estimate"], 0)

    def test_an_oversized_card_is_rejected_with_a_message_naming_the_budget(self):
        response = self.client.put(
            f"/api/v1/personas/{self.persona['id']}/card",
            json={"card_definition": "She bakes bread every morning. " * 200},
        )
        self.assertEqual(response.status_code, 422, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "persona_card_too_large")
        self.assertIn("998", error["message"])
        self.assertIn("3328", error["message"])
        self.assertIn("4096", error["message"])
        unchanged = self.client.get(f"/api/v1/personas/{self.persona['id']}").json()
        self.assertIsNone(unchanged["card_definition"])
        self.assertEqual(unchanged["card_token_estimate"], 0)

    def test_a_card_rejected_at_4096_saves_once_the_allocation_is_raised(self):
        card = {"card_definition": "She bakes bread every morning. " * 120}
        rejected = self.client.put(f"/api/v1/personas/{self.persona['id']}/card", json=card)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        settings = self.client.get("/api/v1/settings").json()
        preferences = dict(settings["preferences"])
        preferences["models_context_window_tokens"] = 8192
        saved_settings = self.client.put("/api/v1/settings", json={**settings, "preferences": preferences})
        self.assertEqual(saved_settings.status_code, 200, saved_settings.text)
        accepted = self.client.put(f"/api/v1/personas/{self.persona['id']}/card", json=card)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["card_cap_tokens"], 2181)

    def test_the_general_persona_route_cannot_set_card_fields(self):
        response = self.client.put(
            f"/api/v1/personas/{self.persona['id']}",
            json={"workspace_id": self.workspace["id"], "name": "Ada", "card_definition": "Bakes bread."},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_another_account_cannot_read_or_write_the_card(self):
        self.client.delete("/api/v1/session")
        self.running.create_and_login("intruder")
        blocked = self.client.put(f"/api/v1/personas/{self.persona['id']}/card", json=SHARED_CARD_FIXTURE)
        self.assertEqual(blocked.status_code, 404, blocked.text)
        self.assertEqual(self.client.get(f"/api/v1/personas/{self.persona['id']}").status_code, 404)


class HistoryFloorTests(unittest.TestCase):
    """The floor is what makes optional prompt material safe to add at any window size."""

    def setUp(self):
        self.service = ContextService(None, None, ContextPolicy(), None)
        self.current = {"role": "user", "content": "Say hello"}

    def _sections(self, size: int):
        return [
            ("example_dialogue", "example " * size),
            ("memory", "memory " * size),
            ("summary", "summary " * size),
        ]

    def test_nothing_is_dropped_when_the_conversation_already_fits(self):
        sections, dropped, remaining = self.service._protect_history_floor(
            [], self._sections(5), self.current, 3328, has_history=True
        )
        self.assertEqual(dropped, ())
        self.assertEqual(len(sections), 3)
        self.assertGreaterEqual(remaining, int(3328 * 0.25))

    def test_sections_yield_in_reverse_authority_until_history_clears_the_floor(self):
        sections, dropped, remaining = self.service._protect_history_floor(
            [], self._sections(400), self.current, 3328, has_history=True
        )
        self.assertEqual(dropped[0], "summary")
        self.assertGreaterEqual(remaining, int(3328 * 0.25))
        self.assertNotIn("summary", {name for name, _text in sections})

    def test_example_dialogue_is_the_last_optional_section_to_go(self):
        _sections, dropped, _remaining = self.service._protect_history_floor(
            [], self._sections(4000), self.current, 3328, has_history=True
        )
        self.assertEqual(dropped, ("summary", "memory", "example_dialogue"))

    def test_a_first_turn_with_no_history_keeps_its_context(self):
        _sections, dropped, _remaining = self.service._protect_history_floor(
            [], self._sections(400), self.current, 3328, has_history=False
        )
        self.assertEqual(dropped, ())

    def test_only_as_many_sections_yield_as_the_floor_requires(self):
        protected = ["[Persona instructions]\n" + ("card " * 300)]
        sections, dropped, remaining = self.service._protect_history_floor(
            protected, self._sections(400), self.current, 3328, has_history=True
        )
        self.assertEqual(dropped, ("summary", "memory"))
        self.assertEqual([name for name, _text in sections], ["example_dialogue"])
        self.assertGreaterEqual(remaining, int(3328 * 0.25))

    def test_the_protected_card_is_never_dropped_to_make_room(self):
        protected = ["[Persona instructions]\n" + ("card " * 300)]
        sections, dropped, _remaining = self.service._protect_history_floor(
            protected, self._sections(4000), self.current, 3328, has_history=True
        )
        self.assertEqual(sections, [])
        self.assertEqual(dropped, ("summary", "memory", "example_dialogue"))
        # The protected list is returned to the caller untouched; only the card's own
        # save-time cap bounds it.
        self.assertEqual(len(protected), 1)


class PersonaExampleDialogueTurnTests(unittest.TestCase):
    def test_example_dialogue_reaches_the_provider_with_placeholders_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Ada"}).json()
                saved = client.put(
                    f"/api/v1/personas/{persona['id']}/card",
                    json={
                        "card_definition": "Runs a bakery.",
                        "card_example_dialogue": "<START>\n{{user}}: You up?\n{{char}}: Barely.\n",
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(saved.json()["example_block_count"], 1)
                self.assertEqual(saved.json()["example_blocks_included"], 1)

                chat = client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Say hello"})
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(running.wait_job(started.json()["job"]["id"])["status"], "completed")

                system = running.chat_provider.requests[0].messages[0]["content"]
                self.assertIn("[Persona voice examples:", system)
                self.assertIn("User: You up?", system)
                self.assertIn("Ada: Barely.", system)
                self.assertNotIn("{{char}}", system)
                # Authored voice examples are not conversation, so they must not be mistaken for
                # transcript by the platform roles that read one.
                for request in running.chat_provider.task_requests:
                    self.assertNotIn("You up?", str(request.messages))

    def test_a_persona_without_example_dialogue_sends_no_example_section(self):
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
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Say hello"})
                running.wait_job(started.json()["job"]["id"])
                self.assertNotIn(
                    "[Persona voice examples:",
                    running.chat_provider.requests[0].messages[0]["content"],
                )


class PersonaCardTurnTests(unittest.TestCase):
    def test_a_card_saved_at_the_cap_still_plans_and_reaches_the_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            with TestApp(Path(tmp)) as running:
                client = running.client
                running.create_and_login()
                workspace = client.post("/api/v1/workspaces", json={"name": "Home"}).json()
                persona = client.post(
                    "/api/v1/personas",
                    json={"workspace_id": workspace["id"], "name": "Ada", "system_prompt": "Answer briefly."},
                ).json()
                # Grow the card until one more sentence would exceed the cap, then save that card.
                sentence = "She opens the bakery before dawn and counts the trays twice. "
                card = {"card_definition": sentence}
                while (
                    card_token_estimate({**card, "card_definition": card["card_definition"] + sentence})
                    <= (persona["card_cap_tokens"])
                ):
                    card["card_definition"] += sentence
                saved = client.put(f"/api/v1/personas/{persona['id']}/card", json=card)
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertGreater(saved.json()["card_token_estimate"], persona["card_cap_tokens"] - 60)

                chat = client.post(
                    "/api/v1/chats",
                    json={"workspace_id": workspace["id"], "persona_id": persona["id"], "title": "New chat"},
                ).json()
                started = client.post(f"/api/v1/chats/{chat['id']}/turns", json={"text": "Say hello"})
                self.assertEqual(started.status_code, 202, started.text)
                job = running.wait_job(started.json()["job"]["id"])
                self.assertEqual(job["status"], "completed", job)
                system = running.chat_provider.requests[0].messages[0]
                self.assertEqual(system["role"], "system")
                self.assertIn("Character definition (facts about who this persona is):", system["content"])


if __name__ == "__main__":
    unittest.main()
