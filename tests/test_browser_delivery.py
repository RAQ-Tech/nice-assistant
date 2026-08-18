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
