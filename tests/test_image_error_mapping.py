import io
import unittest
import urllib.error

from app.server import normalize_image_quality, normalize_image_size, user_safe_image_error


class UserSafeImageErrorTests(unittest.TestCase):
    def test_http_401_returns_api_key_hint(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Invalid API key"}}'),
        )
        message, detail, _ = user_safe_image_error(exc)
        self.assertIn("check API key", message)
        self.assertIn("Invalid API key", detail)

    def test_http_400_includes_provider_detail(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Model not found"}}'),
        )
        message, detail, _ = user_safe_image_error(exc)
        self.assertIn("couldn't generate", message)
        self.assertIn("Model not found", detail)

    def test_url_error_returns_connectivity_message(self):
        exc = urllib.error.URLError("timed out")
        message, _, _ = user_safe_image_error(exc)
        self.assertIn("could not be reached", message)

    def test_safety_errors_are_polished_and_extract_request_id(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                b'{"error":{"message":"Your request was rejected by the safety system. request ID req_abc123 safety_violations=[sexual]"}}'
            ),
        )
        message, detail, req_id = user_safe_image_error(exc)
        self.assertIn("flagged by safety filters", message)
        self.assertIn("safety", detail.lower())
        self.assertEqual(req_id, "req_abc123")


class NormalizeImageQualityTests(unittest.TestCase):
    def test_legacy_values_are_mapped(self):
        self.assertEqual(normalize_image_quality("standard"), "medium")
        self.assertEqual(normalize_image_quality("hd"), "high")

    def test_invalid_values_fall_back_to_auto(self):
        self.assertEqual(normalize_image_quality("ultra"), "auto")
        self.assertEqual(normalize_image_quality(None), "auto")


class NormalizeImageSizeTests(unittest.TestCase):
    def test_invalid_values_fall_back_to_supported_default(self):
        self.assertEqual(normalize_image_size("512x512"), "1024x1024")
        self.assertEqual(normalize_image_size("1024x1024"), "1024x1024")


if __name__ == "__main__":
    unittest.main()
