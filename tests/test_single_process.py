"""The deployment runs as one process, and refuses to pretend otherwise.

Turn event replay, login throttling, and request metrics are held in memory. A
second process would have its own copy of all three. ADR 0034 keeps them there
deliberately; this is the part that stops the assumption being broken by a
configuration change somebody makes without knowing it exists.
"""

from pathlib import Path
import tempfile
import unittest

from app.asgi import create_app
from app.runtime import AppConfig
from app.single_process import WORKER_VARIABLES, multi_process_reason, require_single_process


class MultiProcessRefusalTests(unittest.TestCase):
    def test_a_single_worker_is_what_this_expects(self):
        for variable in WORKER_VARIABLES:
            self.assertEqual(multi_process_reason({variable: "1"}), "")

    def test_more_than_one_worker_is_refused_with_the_reason(self):
        for variable in WORKER_VARIABLES:
            reason = multi_process_reason({variable: "4"})

            self.assertIn("one process", reason)
            self.assertIn("ADR 0034", reason)
            # Names the variable to change, rather than leaving somebody to
            # find which of four it was.
            self.assertIn(variable, reason)

    def test_an_absent_or_empty_value_says_nothing(self):
        self.assertEqual(multi_process_reason({}), "")
        self.assertEqual(multi_process_reason({"WEB_CONCURRENCY": "   "}), "")
        self.assertEqual(multi_process_reason(None), "")

    def test_a_value_it_cannot_read_is_left_alone(self):
        # Refusing to start over something unparseable would be this module
        # deciding it understands a process manager it has never seen.
        self.assertEqual(multi_process_reason({"WEB_CONCURRENCY": "auto"}), "")

    def test_requiring_it_raises_rather_than_returning(self):
        with self.assertRaises(RuntimeError):
            require_single_process({"WEB_CONCURRENCY": "2"})
        require_single_process({"WEB_CONCURRENCY": "1"})


class ApplicationStartupTests(unittest.TestCase):
    def test_the_application_refuses_to_build_under_several_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(data_dir=Path(tmp) / "data", archive_dir=Path(tmp) / "archive")
            import os

            os.environ["WEB_CONCURRENCY"] = "3"
            try:
                with self.assertRaises(RuntimeError) as caught:
                    create_app(config)
                self.assertIn("one process", str(caught.exception))
            finally:
                os.environ.pop("WEB_CONCURRENCY", None)


if __name__ == "__main__":
    unittest.main()
