"""A wider context window by default, without overwriting anybody's choice.

At 4096 tokens, once the reply allowance and the safety margin were reserved,
3328 were left for the system prompt, the persona card, its lorebook, memories
and the entire conversation. Saved context was being dropped on ordinary turns.

Raising the default alone moves nobody: the browser sends every setting on
every save, so a default that was never chosen is stored as though it were.
Migration 0039 corrects exactly those accounts. These pin the line it must not
cross - a value somebody actually picked stays picked.
"""

import json
from pathlib import Path
import unittest

from app.context_policy import ContextPolicy


class DefaultTests(unittest.TestCase):
    def test_the_shipped_default_is_the_wider_one(self):
        # The one literal worth writing down. Everything else derives from it,
        # so this is what makes changing it a deliberate act that shows up in a
        # diff rather than a number that drifts.
        self.assertEqual(ContextPolicy().default_context_window_tokens, 8192)

    def test_the_runtime_config_and_the_policy_agree(self):
        from app.runtime import AppConfig

        # These were two separate literals. Changing one and not the other gave
        # a deployment that disagreed with itself about how much room a persona
        # had, and nothing said so.
        self.assertEqual(AppConfig.default_context_window_tokens, ContextPolicy().default_context_window_tokens)
        self.assertEqual(AppConfig.context_summary_trigger_ratio, ContextPolicy().summary_trigger_ratio)
        self.assertEqual(AppConfig.context_max_compaction_passes, ContextPolicy().max_compaction_passes)

    def test_every_caller_reserves_safety_the_same_way(self):
        from app.context_policy import safety_reserve_tokens

        # Written three different ways in three modules before this, one of
        # them as integer arithmetic that only happened to agree.
        for window in (2048, 4096, 8192, 16384, 32768):
            self.assertEqual(safety_reserve_tokens(window), max(256, -(-window * 5 // 100)))

    def test_the_budget_leaves_real_room_once_a_reply_is_reserved(self):
        from app.context_policy import prompt_budget_tokens

        policy = ContextPolicy()

        # What actually matters is what is left for the persona and the
        # conversation. Under about 4000 it starts dropping saved context.
        budget = prompt_budget_tokens(policy.default_context_window_tokens, policy.output_tokens_default)
        self.assertGreater(budget, 6000)


class BrowserAgreementTests(unittest.TestCase):
    """The browser carries its own copy of these, and it must not drift.

    `SETTINGS_DEFAULTS` exists so the page has something to render before
    settings load. TypeScript cannot import a Python constant, so the second
    copy is unavoidable - but it being unavoidable is not a reason for it to be
    silently wrong. This makes drift a failing build instead of a deployment
    where the page and the server disagree about how much room a persona has.
    """

    def _browser_default(self, key: str) -> str:
        import re

        source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "settings.ts").read_text(encoding="utf-8")
        match = re.search(rf"^\s*{re.escape(key)}:\s*'([^']*)',", source, re.M)
        self.assertIsNotNone(match, f"{key} is no longer in SETTINGS_DEFAULTS")
        return match.group(1)

    def test_the_browser_agrees_about_the_context_window(self):
        policy = ContextPolicy()

        self.assertEqual(
            self._browser_default("models_context_window_tokens"),
            str(policy.default_context_window_tokens),
        )

    def test_the_browser_agrees_about_the_reply_allowance(self):
        policy = ContextPolicy()

        self.assertEqual(self._browser_default("models_num_predict"), str(policy.output_tokens_default))


class MigrationTests(unittest.TestCase):
    """Run the upgrade against a table shaped like the real one."""

    def _migrated(self, rows: dict[str, object]) -> dict[str, dict]:
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        # In memory, so there is no file for Windows to keep locked after the
        # engine goes away. One connection throughout, which is what makes a
        # memory database hold its contents.
        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE app_settings (user_id TEXT PRIMARY KEY, preferences_json TEXT)"))
            for user_id, preferences in rows.items():
                connection.execute(
                    sa.text("INSERT INTO app_settings VALUES (:user_id, :preferences)"),
                    {
                        "user_id": user_id,
                        "preferences": preferences if isinstance(preferences, str) else json.dumps(preferences),
                    },
                )
            context = MigrationContext.configure(connection)
            with Operations.context(Operations(context)):
                _load().upgrade()
            stored = connection.execute(sa.text("SELECT user_id, preferences_json FROM app_settings")).fetchall()
        result = {}
        for user_id, raw in stored:
            try:
                result[user_id] = json.loads(raw)
            except ValueError:
                result[user_id] = {"_unparseable": raw}
        return result

    def test_an_unchanged_default_is_widened(self):
        migrated = self._migrated({"owner": {"models_context_window_tokens": "4096", "general_theme": "dark"}})

        self.assertEqual(migrated["owner"]["models_context_window_tokens"], "8192")
        # Nothing else in the blob is touched.
        self.assertEqual(migrated["owner"]["general_theme"], "dark")

    def test_a_deliberate_choice_is_left_alone(self):
        migrated = self._migrated(
            {
                "small": {"models_context_window_tokens": "2048"},
                "large": {"models_context_window_tokens": "16384"},
            }
        )

        # Somebody who set 2048 because their hardware wants it keeps 2048.
        self.assertEqual(migrated["small"]["models_context_window_tokens"], "2048")
        self.assertEqual(migrated["large"]["models_context_window_tokens"], "16384")

    def test_an_account_that_never_stored_one_is_not_given_one(self):
        migrated = self._migrated({"quiet": {"general_theme": "dark"}})

        # The new default already applies; writing it in would turn a default
        # into a choice, which is the seam this exists to work around.
        self.assertNotIn("models_context_window_tokens", migrated["quiet"])

    def test_unreadable_preferences_do_not_stop_the_upgrade(self):
        migrated = self._migrated(
            {
                "broken": "not json at all",
                "fine": {"models_context_window_tokens": "4096"},
            }
        )

        # A migration that raised here would block an upgrade over one bad row.
        self.assertEqual(migrated["fine"]["models_context_window_tokens"], "8192")


class TruncationTests(unittest.TestCase):
    """A reply that ran out of room says so, instead of looking like a choice."""

    def _turn(self, existing=None):
        from unittest import mock

        from app.context_service import REPLY_TRUNCATED

        turn = mock.Mock(context_degraded_reason=existing)
        service = object.__new__(_context_service())
        uow = mock.MagicMock()
        uow.__enter__.return_value = uow
        uow.repo.turn_by_id.return_value = turn
        service._uow = lambda: uow
        service.record_reply_truncated("turn-1")
        return turn.context_degraded_reason, REPLY_TRUNCATED

    def test_a_truncated_reply_is_recorded(self):
        recorded, marker = self._turn()

        self.assertEqual(recorded, marker)

    def test_it_joins_a_reason_that_is_already_there(self):
        recorded, marker = self._turn("summary_provider_failed")

        # Both facts are true about the turn, and the browser splits on "; ".
        self.assertEqual(recorded, f"summary_provider_failed; {marker}")

    def test_it_is_not_recorded_twice(self):
        recorded, marker = self._turn(f"summary_provider_failed; {marker_of()}")

        self.assertEqual(recorded.count(marker), 1)


def marker_of() -> str:
    from app.context_service import REPLY_TRUNCATED

    return REPLY_TRUNCATED


def _context_service():
    from app.context_service import ContextService

    return ContextService


def _load():
    """Import the revision by path; its filename is not an identifier."""

    import importlib.util

    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0039_wider_default_context_window.py"
    spec = importlib.util.spec_from_file_location("revision_0039", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
