"""ComfyUI is the local image service a new install assumes.

The catalog, recipes and identity work all speak ComfyUI, yet a new install
offered Automatic1111 first and fell back to it in four places on the server,
so the settings page and the Media Catalog page disagreed about what pictures
were made with. The default now lives once on each side of the wire, and this
pins both together.
"""

from pathlib import Path
import re
import unittest

from app.media import DEFAULT_LOCAL_IMAGE_BACKEND, normalize_local_image_backend

FRONTEND_SETTINGS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "settings.ts"


class LocalImageDefaultTests(unittest.TestCase):
    def test_the_shipped_default_is_comfyui(self):
        # The one literal worth writing down; everything else derives from it.
        self.assertEqual(DEFAULT_LOCAL_IMAGE_BACKEND, "comfyui")

    def test_the_browser_assumes_the_same_service(self):
        # TypeScript cannot import a Python constant, so the build reads it.
        match = re.search(r"image_local_backend:\s*'([a-z0-9]+)'", FRONTEND_SETTINGS.read_text(encoding="utf-8"))
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), DEFAULT_LOCAL_IMAGE_BACKEND)

    def test_a_missing_or_unknown_choice_falls_back_to_the_default(self):
        for value in (None, "", "  ", "stable-diffusion", 7):
            self.assertEqual(normalize_local_image_backend(value), DEFAULT_LOCAL_IMAGE_BACKEND)

    def test_a_stored_choice_is_kept(self):
        # Every browser save sends the full settings object, so an install that
        # chose Automatic1111 - or was given it by the old default - keeps it.
        self.assertEqual(normalize_local_image_backend("Automatic1111"), "automatic1111")
        self.assertEqual(normalize_local_image_backend("comfyui"), "comfyui")


if __name__ == "__main__":
    unittest.main()
