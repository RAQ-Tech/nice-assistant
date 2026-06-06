import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobileLayoutStaticTests(unittest.TestCase):
    def test_viewport_meta_allows_safe_area_layout(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("viewport-fit=cover", html)

    def test_css_uses_dynamic_viewport_and_safe_area_variables(self):
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        for token in [
            "--app-viewport-height",
            "--visual-viewport-height",
            "--safe-top",
            "--safe-right",
            "--safe-bottom",
            "--safe-left",
            "env(safe-area-inset-top",
            "100dvh",
            "100svh",
        ]:
            self.assertIn(token, css)
        self.assertIn("height: var(--app-viewport-height)", css)
        self.assertIn("max-height: calc(var(--app-viewport-height)", css)

    def test_app_shell_does_not_use_fixed_100vh_height(self):
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        app_shell_blocks = re.findall(r"\.app-shell\s*\{([^}]*)\}", css, flags=re.DOTALL)
        self.assertTrue(app_shell_blocks)
        for block in app_shell_blocks:
            self.assertNotRegex(block, r"height\s*:\s*100vh\b")

    def test_javascript_tracks_visual_viewport_height(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.visualViewport", js)
        self.assertIn("syncVisualViewportHeight", js)
        self.assertIn("--visual-viewport-height", js)


if __name__ == "__main__":
    unittest.main()
