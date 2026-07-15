import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "src"


class BrowserArchitectureTests(unittest.TestCase):
    def test_browser_is_split_into_typed_modules_and_generated_for_python_packaging(self):
        expected = {
            "api.ts",
            "app.ts",
            "chat.ts",
            "chat_drawer.ts",
            "chat_rendering.ts",
            "capabilities.ts",
            "everyday_settings_view.ts",
            "media.ts",
            "media_catalog_settings_view.ts",
            "identity_settings_view.ts",
            "model_settings_view.ts",
            "operations_settings_view.ts",
            "playback.ts",
            "recording.ts",
            "routing.ts",
            "settings.ts",
            "settings_contracts.ts",
            "settings_controls.ts",
            "settings_ui.ts",
            "settings_view.ts",
            "state.ts",
            "task_model_settings_view.ts",
            "types.ts",
            "visualization.ts",
        }
        self.assertTrue(expected.issubset({path.name for path in SOURCE.glob("*.ts")}))
        self.assertLess((SOURCE / "app.ts").read_text(encoding="utf-8").count("\n"), 650)
        self.assertLess((SOURCE / "identity_settings_view.ts").read_text(encoding="utf-8").count("\n"), 550)
        self.assertLess((SOURCE / "settings_view.ts").read_text(encoding="utf-8").count("\n"), 800)
        self.assertLess((SOURCE / "task_model_settings_view.ts").read_text(encoding="utf-8").count("\n"), 350)
        self.assertLess((SOURCE / "media_catalog_settings_view.ts").read_text(encoding="utf-8").count("\n"), 600)
        self.assertLess((SOURCE / "operations_settings_view.ts").read_text(encoding="utf-8").count("\n"), 450)
        self.assertIn("strict", (ROOT / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertIn('src="/app.js"', (ROOT / "web" / "index.html").read_text(encoding="utf-8"))

    def test_product_source_uses_only_the_canonical_api(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.glob("*.ts"))
        self.assertIn("'/api/v1'", source)
        for legacy in ("/api/login", "/api/chat", "/api/settings", "/api/tts", "/api/stt"):
            self.assertNotIn(legacy, source)


if __name__ == "__main__":
    unittest.main()
