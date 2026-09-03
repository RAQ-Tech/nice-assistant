import tempfile
import unittest
from pathlib import Path

from app.media import (
    normalize_image_quality,
    normalize_image_size,
    normalize_local_image_backend,
    normalize_video_model,
    normalize_video_seconds,
    normalize_video_size,
)
from app.settings import MEDIA_PREFERENCE_CHOICES, validate_media_preferences
from app.service_errors import RequestError
from tests.support import TestApp


class MediaPreferenceValidationTests(unittest.TestCase):
    def test_every_guarded_choice_is_what_the_runtime_actually_honors(self):
        # If a normalizer stops recognizing a value the settings endpoint accepts, saving it
        # would silently produce something else again.
        for size in MEDIA_PREFERENCE_CHOICES["image_size"]:
            self.assertEqual(normalize_image_size(size), size)
        for quality in MEDIA_PREFERENCE_CHOICES["image_quality"]:
            self.assertIsNotNone(normalize_image_quality(quality))
        for backend in MEDIA_PREFERENCE_CHOICES["image_local_backend"]:
            self.assertEqual(normalize_local_image_backend(backend), backend)
        for model in MEDIA_PREFERENCE_CHOICES["video_model"]:
            self.assertEqual(normalize_video_model(model), model)
        for seconds in MEDIA_PREFERENCE_CHOICES["video_duration"]:
            self.assertEqual(normalize_video_seconds(seconds), seconds)
        for size in MEDIA_PREFERENCE_CHOICES["video_size"]:
            model = "sora-2-pro" if size in {"1024x1792", "1792x1024"} else "sora-2"
            self.assertEqual(normalize_video_size(size, model), size)

    def test_absent_and_empty_values_are_left_alone(self):
        validate_media_preferences({})
        validate_media_preferences({"image_size": ""})
        validate_media_preferences({"video_model": None})

    def test_a_value_the_runtime_cannot_honor_is_rejected(self):
        with self.assertRaises(RequestError) as caught:
            validate_media_preferences({"image_size": "4096x4096"})
        self.assertIn("1024x1024", str(caught.exception))
        self.assertIn("4096x4096", str(caught.exception))

    def test_an_unchanged_stored_value_does_not_block_other_settings(self):
        # The browser resubmits every stored value on each save, so an account that predates
        # this validation must not be locked out of saving anything at all.
        stored = {"image_size": "4096x4096"}
        validate_media_preferences({"image_size": "4096x4096", "video_model": "sora-2"}, stored)
        with self.assertRaises(RequestError):
            validate_media_preferences({"image_size": "8192x8192"}, stored)


class MediaSettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _save(self, **preference_overrides):
        settings = self.client.get("/api/v1/settings").json()
        preferences = {**settings["preferences"], **preference_overrides}
        return self.client.put("/api/v1/settings", json={**settings, "preferences": preferences})

    def test_each_guarded_media_preference_is_rejected_when_unusable(self):
        unusable = {
            "image_provider": "stable-diffusion",
            "image_local_backend": "invokeai",
            "image_size": "4096x4096",
            "image_quality": "ultra",
            "video_provider": "runway",
            "video_model": "veo",
            "video_size": "8k",
            "video_duration": "99",
        }
        for key, value in unusable.items():
            response = self._save(**{key: value})
            self.assertEqual(response.status_code, 422, f"{key}: {response.text}")
            message = response.json()["error"]["message"]
            self.assertIn(key, message)
            self.assertIn(value, message)

    def test_a_rejected_save_changes_nothing(self):
        before = self.client.get("/api/v1/settings").json()["preferences"]
        self.assertEqual(self._save(image_size="4096x4096", chat_blur_images=True).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/settings").json()["preferences"], before)

    def test_supported_values_and_legacy_aliases_still_save(self):
        response = self._save(image_provider="local/comfyui", image_size="1024x1536", image_quality="hd")
        self.assertEqual(response.status_code, 200, response.text)
        preferences = response.json()["preferences"]
        self.assertEqual(preferences["image_provider"], "local")
        self.assertEqual(preferences["image_local_backend"], "comfyui")
        self.assertEqual(preferences["image_size"], "1024x1536")

    def test_a_saved_media_choice_is_the_one_generation_uses(self):
        self.assertEqual(self._save(image_size="1536x1024", video_duration="8").status_code, 200)
        preferences = self.client.get("/api/v1/settings").json()["preferences"]
        self.assertEqual(normalize_image_size(preferences["image_size"]), "1536x1024")
        self.assertEqual(normalize_video_seconds(preferences["video_duration"]), "8")

    def test_settings_unrelated_to_media_are_unaffected(self):
        response = self._save(chat_blur_images=True, general_theme="light")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["preferences"]["chat_blur_images"])


class HandsFreePauseTests(unittest.TestCase):
    """The hands-free sending pause is a duration the browser reads (ADR 0038, amended)."""

    def test_the_pause_is_kept_within_reason(self):
        from app.settings import normalize_media_preferences

        self.assertEqual(normalize_media_preferences({"stt_send_pause_ms": "2500"})["stt_send_pause_ms"], 2500)
        # Too short would cut a word; too long would hold the microphone open.
        self.assertEqual(normalize_media_preferences({"stt_send_pause_ms": 100})["stt_send_pause_ms"], 900)
        self.assertEqual(normalize_media_preferences({"stt_send_pause_ms": 99_999})["stt_send_pause_ms"], 900)
        self.assertEqual(normalize_media_preferences({"stt_send_pause_ms": "soon"})["stt_send_pause_ms"], 900)
        # Absent stays absent: the browser's default is the browser's.
        self.assertNotIn("stt_send_pause_ms", normalize_media_preferences({}))


if __name__ == "__main__":
    unittest.main()
