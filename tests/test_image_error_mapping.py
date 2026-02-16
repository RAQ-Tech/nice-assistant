import io
import unittest
import urllib.error

from app.server import user_safe_image_error


class UserSafeImageErrorTests(unittest.TestCase):
    def test_http_401_returns_api_key_hint(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Invalid API key"}}'),
        )
        message = user_safe_image_error(exc)
        self.assertIn("check API key", message)

    def test_http_400_includes_provider_detail(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Model not found"}}'),
        )
        message = user_safe_image_error(exc)
        self.assertIn("Model not found", message)

    def test_url_error_returns_connectivity_message(self):
        exc = urllib.error.URLError("timed out")
        message = user_safe_image_error(exc)
        self.assertIn("could not be reached", message)


if __name__ == "__main__":
    unittest.main()
