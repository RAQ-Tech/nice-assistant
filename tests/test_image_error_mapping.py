import io
import unittest
import urllib.error

from app.server import (
    adjust_prompt_for_local_sd,
    adjust_prompt_for_openai_image,
    extract_model_image_prompt,
    image_prompt_is_detailed,
    local_negative_prompt,
    model_image_instruction_for_provider,
    normalize_image_quality,
    normalize_image_size,
    parse_image_size,
    user_safe_image_error,
    visual_identity_context,
)


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

    def test_parse_image_size_defaults_for_auto(self):
        self.assertEqual(parse_image_size("auto"), (1024, 1024))


class AdjustPromptTests(unittest.TestCase):
    def test_openai_prompt_produces_natural_language_safe_instruction(self):
        adjusted = adjust_prompt_for_openai_image("nsfw nude editorial portrait")
        self.assertIn("Generate a polished", adjusted)
        self.assertIn("fully clothed", adjusted.lower())
        self.assertIn("general audiences", adjusted.lower())
        self.assertNotIn("explicit sexual content", adjusted.lower())
        self.assertNotIn("graphic violence", adjusted.lower())

    def test_local_prompt_adds_quality_tokens_and_sanitizes_when_needed(self):
        adjusted = adjust_prompt_for_local_sd("nude sci-fi portrait", allow_nsfw=False)
        self.assertIn("masterpiece", adjusted.lower())
        self.assertIn("fully clothed", adjusted.lower())

    def test_local_negative_prompt_varies_with_nsfw_toggle(self):
        self.assertIn("nudity", local_negative_prompt(False))
        self.assertNotIn("nudity", local_negative_prompt(True))


class ModelImagePromptPolicyTests(unittest.TestCase):
    def test_extracts_xml_tag_and_strips_from_visible_reply(self):
        clean, prompt = extract_model_image_prompt(
            "Sounds good, check this out. <generate_image>High-fashion evening outfit at a work networking event</generate_image>"
        )
        self.assertEqual(clean, "Sounds good, check this out.")
        self.assertIn("High-fashion evening outfit", prompt)

    def test_returns_original_when_no_tag_present(self):
        clean, prompt = extract_model_image_prompt("Hello there")
        self.assertEqual(clean, "Hello there")
        self.assertEqual(prompt, "")

    def test_prompt_detail_detection(self):
        self.assertFalse(image_prompt_is_detailed("red cat"))
        self.assertTrue(image_prompt_is_detailed("Cinematic portrait shot of a red cat in a library, warm rim lighting, digital illustration style"))

    def test_provider_instruction_mentions_target_style(self):
        self.assertIn("OpenAI image generation", model_image_instruction_for_provider("openai"))
        self.assertIn("Automatic1111", model_image_instruction_for_provider("local"))

    def test_provider_instruction_mentions_visual_continuity(self):
        self.assertIn("visual continuity", model_image_instruction_for_provider("openai").lower())


class VisualIdentityContextTests(unittest.TestCase):
    def test_persona_profile_is_included(self):
        persona = {
            "name": "Ari",
            "traits_json": '{"gender":"female","age":"28"}',
            "personality_details": "Short silver hair and round glasses.",
        }
        hint = visual_identity_context(conn=None, uid="u1", chat_id=None, persona_row=persona)
        self.assertIn("assistant persona is 'Ari'", hint)
        self.assertIn("silver hair", hint.lower())


if __name__ == "__main__":
    unittest.main()
