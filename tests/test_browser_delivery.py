"""A new build has to actually reach the browser.

The container published, the server served the new bundle, and the page kept
running the one from the night before. `/app.js` and `/styles.css` never change
name, and nothing told the browser how long it could keep them - so it applied
its own heuristic freshness and did not even ask.

Ten merges of work were invisible for a day because of this. These pin the
header that fixes it, and the revalidation that makes it cheap.
"""

from pathlib import Path
import tempfile
import unittest

from tests.support import TestApp


class BrowserDeliveryTests(unittest.TestCase):
    def test_the_bundle_is_revalidated_rather_than_assumed_fresh(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            for name in ("app.js", "styles.css", "index.html"):
                response = running.client.get(f"/{name}")

                self.assertEqual(response.status_code, 200, name)
                # "no-cache" is revalidate-before-use, not do-not-store. Without
                # it a browser invents its own expiry for a file whose name
                # never changes, and keeps a stale build for as long as it likes.
                self.assertEqual(response.headers.get("cache-control"), "no-cache", name)

    def test_an_unchanged_bundle_costs_a_header_rather_than_a_download(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            first = running.client.get("/app.js")
            etag = first.headers.get("etag")
            self.assertTrue(etag, "no etag, so revalidation would refetch the whole bundle every load")

            again = running.client.get("/app.js", headers={"If-None-Match": etag})

            # This is what makes revalidating every load acceptable.
            self.assertEqual(again.status_code, 304)
            self.assertEqual(again.content, b"")


class ModuleShapeTests(unittest.TestCase):
    def test_nothing_is_defined_after_the_entry_point(self):
        """A definition below `if __name__ == "__main__"` never runs.

        Under `python -m app.asgi` that block starts uvicorn and blocks, so
        anything after it is simply absent - while the tests, which import the
        module rather than run it, see the whole file and pass. That is exactly
        how a NameError reached a running server and nothing here noticed.
        """

        source = (Path(__file__).resolve().parents[1] / "app" / "asgi.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        entry = next(i for i, line in enumerate(lines) if line.startswith('if __name__ == "__main__":'))
        after = [line for line in lines[entry + 1 :] if line.strip() and not line.startswith((" ", "	"))]

        self.assertEqual(after, [], f"defined after the entry point and therefore never loaded: {after}")


if __name__ == "__main__":
    unittest.main()


class InstallableAppTests(unittest.TestCase):
    """What a phone needs to put the app on its home screen. ADR 0050."""

    def test_the_manifest_names_the_app_and_its_icons(self):
        import json

        from PIL import Image

        web = Path(__file__).resolve().parents[1] / "web"
        manifest = json.loads((web / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual((manifest["start_url"], manifest["scope"]), ("/", "/"))
        self.assertTrue(manifest["name"] and manifest["short_name"])
        purposes = set()
        for entry in manifest["icons"]:
            width, height = (int(value) for value in entry["sizes"].split("x"))
            with Image.open(web / entry["src"].lstrip("/")) as picture:
                # A declared size the file does not have is an icon Chrome rejects.
                self.assertEqual(picture.size, (width, height), entry["src"])
            purposes.add((entry.get("purpose", "any"), width))
        self.assertTrue({("any", 192), ("any", 512), ("maskable", 512)} <= purposes)

        page = (web / "index.html").read_text(encoding="utf-8")
        for link in ('rel="manifest"', 'rel="apple-touch-icon"', 'name="theme-color"'):
            self.assertIn(link, page)

    def test_the_server_hands_them_out_with_the_right_types(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            manifest = running.client.get("/manifest.webmanifest")
            self.assertEqual(manifest.status_code, 200)
            self.assertEqual(manifest.headers["content-type"], "application/manifest+json")
            for path, kind in (
                ("/sw.js", "javascript"),
                ("/offline.html", "text/html"),
                ("/icons/icon-192.png", "image/png"),
            ):
                response = running.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn(kind, response.headers["content-type"], path)
                # The service worker in particular must never be kept stale.
                self.assertEqual(response.headers.get("cache-control"), "no-cache", path)

    def test_the_service_worker_keeps_nothing_but_the_offline_page(self):
        worker = (Path(__file__).resolve().parents[1] / "frontend" / "public" / "sw.js").read_text(encoding="utf-8")
        # A cached copy of the app would outlive an update; only this page is stored.
        self.assertEqual(worker.count("cache.add("), 1)
        self.assertIn("cache.add(OFFLINE)", worker)
        self.assertNotIn("cache.put(", worker)
        self.assertIn("if (event.request.mode !== 'navigate') return;", worker)
