import unittest

from app.compreface_identity_provider import CompreFaceIdentityProvider, normalize_compreface_base_url
from app.provider_contracts import ProviderError


class CompreFaceIdentityProviderTests(unittest.TestCase):
    def test_url_normalization_rejects_credentials_and_non_http_urls(self):
        self.assertEqual(normalize_compreface_base_url("http://verifier.lan:8000/"), "http://verifier.lan:8000")
        for value in ("file:///tmp/verifier", "http://user:password@verifier.lan", "http://verifier.lan/?key=x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_compreface_base_url(value)

    def test_verification_response_uses_best_similarity_and_safe_metadata(self):
        result = CompreFaceIdentityProvider()._parse(
            {
                "result": [
                    {"face_matches": [{"similarity": 0.42}, {"similarity": 0.87}]},
                    {"face_matches": [{"similarity": 0.66}]},
                ],
                "plugins_versions": {"facenet": "1"},
            }
        )
        self.assertEqual(result.similarity, 0.87)
        self.assertEqual(result.source_face_count, 2)
        self.assertEqual(result.target_face_count, 3)
        self.assertNotIn("image", str(result))

    def test_missing_faces_and_auth_errors_are_redacted(self):
        provider = CompreFaceIdentityProvider()
        with self.assertRaises(ProviderError) as missing:
            provider._parse({"result": []})
        self.assertEqual(missing.exception.code, "identity_face_not_detected")
        auth = provider._provider_error(401)
        self.assertEqual(auth.code, "identity_provider_auth_failed")
        self.assertNotIn("key", auth.user_message.lower())


if __name__ == "__main__":
    unittest.main()
