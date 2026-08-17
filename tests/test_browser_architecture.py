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
            "home_controls.ts",
            "home_view.ts",
            "onboarding.ts",
            "chat_rendering.ts",
            "capabilities.ts",
            "everyday_settings_view.ts",
            "media.ts",
            "media_catalog_settings_view.ts",
            "identity_settings_view.ts",
            "model_settings_view.ts",
            "operations_settings_view.ts",
            "persona_card.ts",
            "persona_card_view.ts",
            "persona_lore_view.ts",
            "preset_settings_view.ts",
            "playback.ts",
            "recording.ts",
            "routing.ts",
            "settings.ts",
            "settings_contracts.ts",
            "settings_controls.ts",
            "settings_ui.ts",
            "settings_view.ts",
            "state.ts",
            "streaming_audio.ts",
            "talk_button.ts",
            "task_model_settings_view.ts",
            "turn_detection.ts",
            "types.ts",
            "visualization.ts",
            "workflow_template_view.ts",
        }
        self.assertTrue(expected.issubset({path.name for path in SOURCE.glob("*.ts")}))
        self.assertLess((SOURCE / "app.ts").read_text(encoding="utf-8").count("\n"), 650)
        self.assertLess((SOURCE / "identity_settings_view.ts").read_text(encoding="utf-8").count("\n"), 550)
        self.assertLess((SOURCE / "settings_view.ts").read_text(encoding="utf-8").count("\n"), 800)
        self.assertLess((SOURCE / "task_model_settings_view.ts").read_text(encoding="utf-8").count("\n"), 350)
        self.assertLess((SOURCE / "media_catalog_settings_view.ts").read_text(encoding="utf-8").count("\n"), 600)
        self.assertLess((SOURCE / "persona_card_view.ts").read_text(encoding="utf-8").count("\n"), 150)
        self.assertLess((SOURCE / "persona_lore_view.ts").read_text(encoding="utf-8").count("\n"), 300)
        self.assertLess((SOURCE / "preset_settings_view.ts").read_text(encoding="utf-8").count("\n"), 550)
        self.assertLess((SOURCE / "home_view.ts").read_text(encoding="utf-8").count("\n"), 300)
        self.assertLess((SOURCE / "home_controls.ts").read_text(encoding="utf-8").count("\n"), 250)
        self.assertLess((SOURCE / "operations_settings_view.ts").read_text(encoding="utf-8").count("\n"), 450)
        self.assertLess((SOURCE / "workflow_template_view.ts").read_text(encoding="utf-8").count("\n"), 250)
        self.assertLess((SOURCE / "streaming_audio.ts").read_text(encoding="utf-8").count("\n"), 150)
        self.assertLess((SOURCE / "turn_detection.ts").read_text(encoding="utf-8").count("\n"), 180)
        self.assertLess((SOURCE / "recording.ts").read_text(encoding="utf-8").count("\n"), 220)
        self.assertLess((SOURCE / "playback.ts").read_text(encoding="utf-8").count("\n"), 250)
        self.assertLess((SOURCE / "identity_workflow_setup_view.ts").read_text(encoding="utf-8").count("\n"), 520)
        self.assertIn("strict", (ROOT / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertIn('src="/app.js"', (ROOT / "web" / "index.html").read_text(encoding="utf-8"))

    def test_the_stylesheet_closes_every_rule_it_opens(self):
        """An unclosed rule silently deletes every rule after it.

        A missing brace in the middle of the file swallowed everything that
        followed into one invalid declaration block: the home mark rendered at
        its intrinsic 256px because the rule sizing it never applied, and the
        whole homepage was unstyled. Nothing failed - CSS discards what it
        cannot parse and carries on - so the only way to notice was to look.
        """

        text = (SOURCE / "styles.css").read_text(encoding="utf-8")
        depth = 0
        for number, line in enumerate(text.splitlines(), 1):
            depth += line.count("{") - line.count("}")
            self.assertGreaterEqual(depth, 0, f"styles.css:{number} closes a rule that was never opened")
        self.assertEqual(depth, 0, f"styles.css leaves {depth} rule(s) open")

    def test_product_source_uses_only_the_canonical_api(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.glob("*.ts"))
        self.assertIn("'/api/v1'", source)
        for legacy in ("/api/login", "/api/chat", "/api/settings", "/api/tts", "/api/stt"):
            self.assertNotIn(legacy, source)


if __name__ == "__main__":
    unittest.main()
