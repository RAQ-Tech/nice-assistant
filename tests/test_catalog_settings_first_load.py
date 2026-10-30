"""A new account's first page load does not race itself into a 500.

The browser asks for the media catalog and for picture readiness together the
first time an account opens the app. Each found no catalog settings row, each
inserted one, and the second failed the unique key - so on a fresh install the
first readiness answer was a server error and the Image Generation page had
nothing to say.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.models import MediaCatalogSetting
from app.repositories import UnitOfWork
from tests.support import TestApp


class FirstLoadTests(unittest.TestCase):
    def test_the_request_that_loses_the_race_reads_the_row_the_other_made(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            runtime = running.services.runtime

            with UnitOfWork(runtime.session_factory, runtime.secret_store) as winner:
                winner.repo.save_media_catalog_setting(user_id, {"max_loras": 7})

            with UnitOfWork(runtime.session_factory, runtime.secret_store) as loser:
                session = loser.repo.session
                real_get = session.get
                looked = []

                # The loser looked before the winner committed, so its first
                # look found nothing.
                def stale_first_look(model, key, **kwargs):
                    looked.append(model)
                    return None if len(looked) == 1 else real_get(model, key, **kwargs)

                with mock.patch.object(session, "get", side_effect=stale_first_look):
                    row = loser.repo.media_catalog_setting(user_id)

                self.assertIsInstance(row, MediaCatalogSetting)
                # The winner's row, not a second one and not a failure.
                self.assertEqual(row.max_loras, 7)

    def test_the_first_load_answers_readiness_and_the_catalog(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self.assertEqual(running.client.get("/api/v1/media/readiness").status_code, 200)
            self.assertEqual(running.client.get("/api/v1/media-catalog").status_code, 200)


if __name__ == "__main__":
    unittest.main()
