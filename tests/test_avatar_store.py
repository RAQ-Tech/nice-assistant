"""The product owns a copy of every persona's face.

An avatar was a URL, and the image lived wherever the URL pointed. One pointed
into ComfyUI's output folder; ComfyUI reorganised, and a persona's face
silently vanished with nothing deleted anywhere. These pin the fix: a pasted
URL is copied into a store this product owns, personas that predate the store
are converted by the background pass, and a source being down is a wait rather
than an error or a lost save.
"""

import base64
from pathlib import Path
import tempfile
import unittest

from app.avatar_store import (
    AvatarUnavailable,
    avatar_file,
    is_served,
    refresh_from_file,
    snapshot,
    store_bytes,
)
from tests.support import TestApp

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


class StoreTests(unittest.TestCase):
    def test_a_data_url_becomes_a_file_and_a_served_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = snapshot(Path(tmp), "p1", DATA_URL)

            self.assertTrue(url.startswith("/api/v1/personas/p1/avatar?v="))
            self.assertEqual(avatar_file(Path(tmp), "p1").read_bytes(), PNG)

    def test_a_remote_image_is_fetched_once_and_kept(self):
        def fake_fetch(url):
            return PNG, "image/png"

        with tempfile.TemporaryDirectory() as tmp:
            url = snapshot(Path(tmp), "p1", "http://elsewhere.invalid/face.png", fetch=fake_fetch)

            self.assertTrue(is_served(url))
            self.assertEqual(avatar_file(Path(tmp), "p1").read_bytes(), PNG)

    def test_an_answer_that_is_not_an_image_is_refused(self):
        def html_fetch(url):
            return b"<html>404</html>", "text/html"

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AvatarUnavailable):
                snapshot(Path(tmp), "p1", "http://elsewhere.invalid/gone", fetch=html_fetch)
            # A refusal leaves nothing behind to be mistaken for a face.
            self.assertIsNone(avatar_file(Path(tmp), "p1"))

    def test_a_replacement_in_a_new_format_retires_the_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_bytes(Path(tmp), "p1", PNG, "image/png")
            store_bytes(Path(tmp), "p1", PNG, "image/webp")

            self.assertEqual(avatar_file(Path(tmp), "p1").suffix, ".webp")
            self.assertFalse((Path(tmp) / "p1.png").exists())

    def test_a_file_placed_by_hand_is_adoptable(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The restore path: the source is gone for good, but the operator
            # still has the picture and drops it into the store directly.
            (Path(tmp) / "p1.png").write_bytes(PNG)

            self.assertTrue(is_served(refresh_from_file(Path(tmp), "p1")))
            self.assertIsNone(refresh_from_file(Path(tmp), "nobody"))

    def test_the_version_in_the_url_follows_the_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = store_bytes(Path(tmp), "p1", PNG, "image/png")
            same = refresh_from_file(Path(tmp), "p1")
            changed = store_bytes(Path(tmp), "p1", PNG + b"\x00", "image/png")

        # Hard caching is only safe because a changed picture is a new URL.
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)


class ThroughTheProductTests(unittest.TestCase):
    def test_a_pasted_data_url_is_adopted_at_save(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()

            saved = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "April", "avatar_url": DATA_URL},
            ).json()

            # The multi-megabyte blob stays out of the database and out of
            # every persona listing from now on.
            self.assertTrue(is_served(saved["avatar_url"]), saved["avatar_url"])
            served = running.client.get(saved["avatar_url"])
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.content, PNG)

    def test_an_unreachable_url_costs_nothing_but_time(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            running.services.resources.avatar_fetch = lambda url: (_ for _ in ()).throw(
                AvatarUnavailable("down right now")
            )

            saved = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "April",
                    "avatar_url": "http://127.0.0.1:9/face.png",
                },
            )

            # The save succeeds and the URL is kept as pasted, so the
            # background pass can adopt it once the source answers again.
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["avatar_url"], "http://127.0.0.1:9/face.png")

    def test_the_background_pass_adopts_what_save_could_not(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            resources = running.services.resources
            resources.avatar_fetch = lambda url: (_ for _ in ()).throw(AvatarUnavailable("down"))
            persona = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": workspace["id"],
                    "name": "April",
                    "avatar_url": "http://127.0.0.1:9/face.png",
                },
            ).json()

            waiting = resources.snapshot_pending_avatars()
            resources.avatar_fetch = lambda url: (PNG, "image/png")
            healed = resources.snapshot_pending_avatars()

            self.assertEqual(waiting["waiting"], 1)
            self.assertEqual(healed["converted"], 1)
            refreshed = running.client.get("/api/v1/personas").json()["items"]
            row = next(item for item in refreshed if item["id"] == persona["id"])
            self.assertTrue(is_served(row["avatar_url"]))

    def test_another_account_cannot_read_the_stored_face(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
            saved = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "April", "avatar_url": DATA_URL},
            ).json()
            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")

            blocked = running.client.get(f"/api/v1/personas/{saved['id']}/avatar")

            self.assertEqual(blocked.status_code, 404, blocked.text)


if __name__ == "__main__":
    unittest.main()
