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
    normalize_local_image_base_url,
    normalize_image_size,
    parse_additional_parameters,
    parse_image_size,
    user_safe_image_error,
    user_safe_video_error,
    looks_like_video_request,
    visual_identity_context,
)


class UserSafeImageErrorTests(unittest.TestCase):
    def test_openai_http_401_still_mentions_openai_key(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/images/generations",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"Invalid API key"}}'),
        )
        message, _, _ = user_safe_image_error(exc, provider="openai")
        self.assertIn("OpenAI rejected", message)

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




class LocalImageBaseUrlTests(unittest.TestCase):
    def test_blank_base_url_uses_server_default(self):
        normalized = normalize_local_image_base_url('')
        self.assertTrue(normalized.startswith('http'))

    def test_invalid_base_url_raises(self):
        with self.assertRaises(ValueError):
            normalize_local_image_base_url('automatic1111:7860')


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




class LocalParameterParsingTests(unittest.TestCase):
    def test_parse_image_size_accepts_custom_when_enabled(self):
        self.assertEqual(parse_image_size("768x1152", allow_custom=True), (768, 1152))

    def test_additional_parameters_requires_object(self):
        self.assertEqual(parse_additional_parameters('{"enable_hr": true}')["enable_hr"], True)
        with self.assertRaises(ValueError):
            parse_additional_parameters('[1,2,3]')


class LocalProviderErrorMappingTests(unittest.TestCase):
    def test_local_unauthorized_mentions_auth(self):
        exc = urllib.error.HTTPError(
            url="http://localhost:7860/sdapi/v1/txt2img",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"Not authenticated"}'),
        )
        message, detail, _ = user_safe_image_error(exc, provider="local")
        self.assertIn("authentication", message.lower())
        self.assertIn("Not authenticated", detail)

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

    def test_memory_cues_are_scoped_to_chat_persona_and_workspace(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE memories (user_id TEXT, tier TEXT, tier_ref_id TEXT, content TEXT, created_at INTEGER)")
        conn.execute("CREATE TABLE messages (chat_id TEXT, text TEXT, created_at INTEGER)")
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", ("u1", "chat", "chat-1", "I wear a green scarf", 1))
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", ("u1", "persona", "persona-1", "My hair is short", 2))
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", ("u1", "workspace", "workspace-1", "I have blue eyes", 3))
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", ("u1", "persona", "persona-2", "I wear a red hat", 4))
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", ("u1", "workspace", "workspace-2", "I have purple hair", 5))
        conn.execute("INSERT INTO messages VALUES (?,?,?)", ("chat-1", "My avatar has freckles", 6))
        conn.commit()

        hint = visual_identity_context(
            conn=conn,
            uid="u1",
            chat_id="chat-1",
            persona_row=None,
            workspace_id="workspace-1",
            persona_id="persona-1",
        )

        self.assertIn("green scarf", hint.lower())
        self.assertIn("blue eyes", hint.lower())
        self.assertIn("freckles", hint.lower())
        self.assertNotIn("red hat", hint.lower())
        self.assertNotIn("purple hair", hint.lower())

        conn.close()


if __name__ == "__main__":
    unittest.main()


class VideoRequestAndErrorTests(unittest.TestCase):
    def test_video_intent_detection(self):
        self.assertTrue(looks_like_video_request("Please generate a video of a rollercoaster"))
        self.assertFalse(looks_like_video_request("Please explain rollercoasters"))

    def test_video_error_mapping_never_mentions_automatic1111(self):
        exc = urllib.error.HTTPError(
            url="https://api.openai.com/v1/videos",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"temporary outage"}}'),
        )
        message, detail, _ = user_safe_video_error(exc)
        self.assertIn("OpenAI", message)
        self.assertNotIn("Automatic1111", message)
        self.assertIn("temporary outage", detail)
