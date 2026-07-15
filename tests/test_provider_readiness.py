import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.support import TestApp


class ProviderReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.running.create_and_login()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_ollama_health_uses_provider_contract(self):
        response = self.running.client.post("/api/v1/provider-checks", json={"provider": "ollama"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["status"], "ready")

    def test_openai_missing_key_and_safe_http_error(self):
        missing = self.running.client.post("/api/v1/provider-checks", json={"provider": "openai"})
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json()["status"], "missing")
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/models",
            401,
            "Unauthorized sk-secret-value",
            {},
            io.BytesIO(b'{"error":{"message":"bad key sk-secret-value"}}'),
        )
        with mock.patch("app.provider_service.urllib.request.urlopen", side_effect=error):
            failed = self.running.client.post(
                "/api/v1/provider-checks",
                json={"provider": "openai", "settings": {"openai_api_key": "sk-test-value"}},
            )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertNotIn("sk-secret", str(failed.json()))

    def test_local_provider_aliases_and_unknown_provider(self):
        with mock.patch("app.provider_service.provider_get_json", return_value={"data": [{"id": "af_heart"}]}):
            kokoro = self.running.client.post(
                "/api/v1/provider-checks",
                json={"provider": "kokoro", "settings": {"preferences": {}}},
            )
            automatic = self.running.client.post("/api/v1/provider-checks", json={"provider": "a1111"})
        self.assertTrue(kokoro.json()["ok"])
        self.assertEqual(automatic.json()["provider"], "automatic1111")
        self.assertEqual(
            self.running.client.post("/api/v1/provider-checks", json={"provider": "unknown"}).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
