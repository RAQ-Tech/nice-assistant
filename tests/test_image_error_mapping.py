import io
import base64
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from app.media import (
    adjust_prompt_for_local_sd,
    adjust_prompt_for_openai_image,
    clean_user_image_prompt,
    local_seed_for_backend,
    local_negative_prompt,
    normalize_image_quality,
    normalize_local_image_backend,
    normalize_image_size,
    normalize_video_model,
    normalize_video_seconds,
    normalize_video_size,
    parse_additional_parameters,
    parse_image_size,
    user_safe_image_error,
    user_safe_video_error,
)
from app.media import normalize_local_image_base_url as _normalize_local_image_base_url
from app.media_clients import automatic1111_image, comfyui_image, openai_video
from app.provider_contracts import CancellationToken, ProviderError


def normalize_local_image_base_url(value):
    return _normalize_local_image_base_url(value, "http://127.0.0.1:7860")


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
        normalized = normalize_local_image_base_url("")
        self.assertTrue(normalized.startswith("http"))

    def test_invalid_base_url_raises(self):
        with self.assertRaises(ValueError):
            normalize_local_image_base_url("automatic1111:7860")


class LocalImageBackendTests(unittest.TestCase):
    def test_backend_defaults_to_automatic1111(self):
        self.assertEqual(normalize_local_image_backend(""), "automatic1111")
        self.assertEqual(normalize_local_image_backend("unknown"), "automatic1111")

    def test_known_backends_are_supported(self):
        self.assertEqual(normalize_local_image_backend("automatic1111"), "automatic1111")
        self.assertEqual(normalize_local_image_backend("ComfyUI"), "comfyui")


class NormalizeImageQualityTests(unittest.TestCase):
    def test_legacy_values_are_mapped(self):
        self.assertEqual(normalize_image_quality("standard"), "medium")
        self.assertEqual(normalize_image_quality("hd"), "high")

    def test_invalid_values_fall_back_to_auto(self):
        self.assertEqual(normalize_image_quality("ultra"), "auto")
        self.assertEqual(normalize_image_quality(None), "auto")

    def test_none_quality_is_supported(self):
        self.assertEqual(normalize_image_quality("none"), "none")


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
            parse_additional_parameters("[1,2,3]")


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

    def test_comfyui_not_found_mentions_routes(self):
        exc = urllib.error.HTTPError(
            url="http://localhost:8188/prompt",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"route missing"}'),
        )
        message, detail, _ = user_safe_image_error(exc, provider="local/comfyui")
        self.assertIn("ComfyUI route not found", message)
        self.assertIn("route missing", detail)


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

    def test_none_quality_disables_local_prompt_enhancements(self):
        adjusted = adjust_prompt_for_local_sd("the following prompt: dog running", allow_nsfw=False, quality="none")
        self.assertEqual(adjusted.lower(), "dog running")
        self.assertEqual(local_negative_prompt(False, quality="none"), "")

    def test_clean_user_prompt_strips_prompt_wrappers(self):
        cleaned = clean_user_image_prompt("please generate an image with the following prompt: dog running")
        self.assertEqual(cleaned.lower(), "dog running")


class LocalSeedTests(unittest.TestCase):
    def test_comfyui_seed_randomizes_with_empty_or_negative_one(self):
        seed_blank = local_seed_for_backend("", "comfyui")
        seed_negative = local_seed_for_backend("-1", "comfyui")
        self.assertIsInstance(seed_blank, int)
        self.assertIsInstance(seed_negative, int)
        self.assertGreater(seed_blank, 0)
        self.assertGreater(seed_negative, 0)

    def test_automatic1111_keeps_negative_one_seed(self):
        self.assertEqual(local_seed_for_backend("-1", "automatic1111"), -1)


class LocalLoraExecutionTests(unittest.TestCase):
    def test_automatic1111_receives_catalog_lora_prompt_syntax_and_triggers(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"images": [base64.b64encode(b"image").decode()]}).encode()

        def fake_urlopen(request, timeout=0):
            captured.update(json.loads(request.data.decode()))
            return Response()

        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(
                automatic1111_image(
                    "portrait",
                    "512x512",
                    "none",
                    True,
                    "http://automatic1111:7860",
                    {"loras": [{"name": "identity.safetensors", "weight": 0.8, "trigger_words": ["nova person"]}]},
                ),
                b"image",
            )
        self.assertIn("nova person", captured["prompt"])
        self.assertIn("<lora:identity.safetensors:0.8>", captured["prompt"])

    def test_comfyui_builds_a_real_lora_loader_chain(self):
        captured = {}

        class Response:
            def __init__(self, payload=None, content=None):
                self.payload = payload
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.content if self.content is not None else json.dumps(self.payload).encode()

        def fake_urlopen(request, timeout=0):
            if request.full_url.endswith("/prompt"):
                captured.update(json.loads(request.data.decode())["prompt"])
                return Response({"prompt_id": "prompt-1"})
            if "/history/" in request.full_url:
                return Response({"prompt-1": {"outputs": {"9": {"images": [{"filename": "x.png"}]}}}})
            if "/view?" in request.full_url:
                return Response(content=b"comfy-image")
            raise AssertionError(request.full_url)

        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(
                comfyui_image(
                    "portrait",
                    "512x512",
                    "none",
                    True,
                    "http://comfyui:8188",
                    {"loras": [{"name": "identity.safetensors", "weight": 0.7, "trigger_words": ["nova person"]}]},
                ),
                b"comfy-image",
            )
        lora_id = next(key for key, node in captured.items() if node.get("class_type") == "LoraLoader")
        self.assertEqual(captured[lora_id]["inputs"]["lora_name"], "identity.safetensors")
        self.assertEqual(captured["3"]["inputs"]["model"], [lora_id, 0])
        self.assertEqual(captured["6"]["inputs"]["clip"], [lora_id, 1])

    def test_comfyui_lora_nodes_do_not_collide_with_operator_workflow_nodes(self):
        captured = {}

        class Response:
            def __init__(self, payload=None, content=None):
                self.payload = payload
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.content if self.content is not None else json.dumps(self.payload).encode()

        def fake_urlopen(request, timeout=0):
            if request.full_url.endswith("/prompt"):
                captured.update(json.loads(request.data.decode())["prompt"])
                return Response({"prompt_id": "prompt-1"})
            if "/history/" in request.full_url:
                return Response({"prompt-1": {"outputs": {"9": {"images": [{"filename": "x.png"}]}}}})
            if "/view?" in request.full_url:
                return Response(content=b"comfy-image")
            raise AssertionError(request.full_url)

        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=fake_urlopen):
            comfyui_image(
                "portrait",
                "512x512",
                "none",
                True,
                "http://comfyui:8188",
                {
                    "loras": [{"name": "identity.safetensors", "weight": 0.7}],
                    "additional_parameters": json.dumps(
                        {"1000": {"class_type": "LoadImage", "inputs": {"image": "placeholder.jpg"}}}
                    ),
                },
            )
        self.assertEqual(captured["1000"]["class_type"], "LoadImage")
        lora_id = next(key for key, node in captured.items() if node.get("class_type") == "LoraLoader")
        self.assertNotEqual(lora_id, "1000")

    def test_comfyui_uploads_reviewed_identity_and_injects_declared_binding(self):
        captured = {"upload": None, "workflow": None}

        class Response:
            def __init__(self, payload=None, content=None):
                self.payload = payload
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def close(self):
                return None

            def read(self):
                return self.content if self.content is not None else json.dumps(self.payload).encode()

        def fake_urlopen(request, timeout=0):
            if request.full_url.endswith("/upload/image"):
                captured["upload"] = request.data
                return Response({"name": "avery.jpg", "subfolder": "nice-identities", "type": "input"})
            if request.full_url.endswith("/prompt"):
                captured["workflow"] = json.loads(request.data.decode())["prompt"]
                return Response({"prompt_id": "prompt-identity"})
            if "/history/" in request.full_url:
                return Response({"prompt-identity": {"outputs": {"9": {"images": [{"filename": "identity.png"}]}}}})
            if "/view?" in request.full_url:
                return Response(content=b"conditioned-image")
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reviewed.jpg"
            reference.write_bytes(b"reviewed-reference-content")
            settings = {
                "identity_reference_path": str(reference),
                "identity_reference_sha256": sha256(reference.read_bytes()).hexdigest(),
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
                "additional_parameters": json.dumps(
                    {"100": {"class_type": "LoadImage", "inputs": {"image": "placeholder.jpg"}}}
                ),
            }
            with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=fake_urlopen):
                content = comfyui_image(
                    "portrait",
                    "512x512",
                    "none",
                    True,
                    "http://comfyui:8188",
                    settings,
                    CancellationToken(),
                )
        self.assertEqual(content, b"conditioned-image")
        self.assertIn(b"reviewed-reference-content", captured["upload"])
        self.assertEqual(captured["workflow"]["100"]["inputs"]["image"], "nice-identities/avery.jpg")

    def test_comfyui_identity_stage_honors_cancellation_before_upload(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(ProviderError) as raised:
            comfyui_image(
                "portrait",
                "512x512",
                "none",
                True,
                "http://comfyui:8188",
                {
                    "identity_reference_path": "missing.jpg",
                    "identity_reference_sha256": "0" * 64,
                    "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
                },
                token,
            )
        self.assertEqual(raised.exception.code, "cancelled")

    def test_comfyui_uploads_and_injects_owner_selected_source_and_mask(self):
        captured = {"workflow": None, "uploads": []}

        class Response:
            def __init__(self, payload=None, content=None):
                self.payload = payload
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def close(self):
                return None

            def read(self):
                return self.content if self.content is not None else json.dumps(self.payload).encode()

        def fake_urlopen(request, timeout=0):
            if request.full_url.endswith("/upload/image"):
                captured["uploads"].append(request.data)
                name = "source.png" if b"source-image" in request.data else "mask.png"
                return Response({"name": name, "subfolder": "nice-edits", "type": "input"})
            if request.full_url.endswith("/prompt"):
                captured["workflow"] = json.loads(request.data.decode())["prompt"]
                return Response({"prompt_id": "prompt-edit"})
            if "/history/" in request.full_url:
                return Response({"prompt-edit": {"outputs": {"9": {"images": [{"filename": "edit.png"}]}}}})
            if "/view?" in request.full_url:
                return Response(content=b"edited-image")
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            mask = Path(tmp) / "mask.png"
            source.write_bytes(b"source-image")
            mask.write_bytes(b"mask-image")
            settings = {
                "source_image_path": str(source),
                "source_image_sha256": sha256(source.read_bytes()).hexdigest(),
                "source_image_bindings": [{"node_id": "100", "input_name": "image"}],
                "mask_image_path": str(mask),
                "mask_image_sha256": sha256(mask.read_bytes()).hexdigest(),
                "mask_image_bindings": [{"node_id": "101", "input_name": "image"}],
                "additional_parameters": json.dumps(
                    {
                        "100": {"class_type": "LoadImage", "inputs": {"image": "source-placeholder.png"}},
                        "101": {"class_type": "LoadImage", "inputs": {"image": "mask-placeholder.png"}},
                    }
                ),
            }
            with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=fake_urlopen):
                content = comfyui_image(
                    "edit portrait", "512x512", "none", True, "http://comfyui:8188", settings, CancellationToken()
                )
        self.assertEqual(content, b"edited-image")
        self.assertEqual(len(captured["uploads"]), 2)
        self.assertEqual(captured["workflow"]["100"]["inputs"]["image"], "nice-edits/source.png")
        self.assertEqual(captured["workflow"]["101"]["inputs"]["image"], "nice-edits/mask.png")


class VideoRequestAndErrorTests(unittest.TestCase):
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


class NormalizeVideoSettingsTests(unittest.TestCase):
    def test_video_model_normalization(self):
        self.assertEqual(normalize_video_model("sora-2"), "sora-2")
        self.assertEqual(normalize_video_model("SORA-2-PRO"), "sora-2-pro")
        self.assertEqual(normalize_video_model("unknown"), "sora-2")

    def test_video_seconds_normalization(self):
        self.assertEqual(normalize_video_seconds("4"), "4")
        self.assertEqual(normalize_video_seconds("8"), "8")
        self.assertEqual(normalize_video_seconds("9"), "4")

    def test_video_size_normalization_for_model(self):
        self.assertEqual(normalize_video_size("720x1280", "sora-2"), "720x1280")
        self.assertEqual(normalize_video_size("1024x1792", "sora-2"), "720x1280")
        self.assertEqual(normalize_video_size("1792x1024", "sora-2-pro"), "1792x1024")


class OpenAIVideoRequestTests(unittest.TestCase):
    def test_openai_video_retries_with_minimal_payload_on_http_400(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
                self.headers = {"Content-Type": "application/json"}

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        calls = []

        def fake_urlopen(req, timeout=0):
            if isinstance(req, str):
                url = req
                calls.append((url, "GET", ""))
            else:
                url = req.full_url
                calls.append((url, req.method, req.data.decode("utf-8") if req.data else ""))
            if url == "https://api.openai.com/v1/videos":
                body = req.data.decode("utf-8") if req.data else "{}"
                payload = json.loads(body)
                if set(payload.keys()) != {"model", "prompt"}:
                    raise urllib.error.HTTPError(
                        url=url,
                        code=400,
                        msg="Bad Request",
                        hdrs=None,
                        fp=io.BytesIO(b'{"error":{"message":"unknown parameter: duration"}}'),
                    )
                return FakeResponse({"url": "https://cdn.example.com/video.mp4"})
            if url == "https://cdn.example.com/video.mp4":
                resp = FakeResponse({})
                resp.headers = {"Content-Type": "video/mp4"}
                resp.read = lambda: b"video-bytes"
                return resp
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            data, ext = openai_video(
                prompt="a dog chasing a cat",
                size="1024x1024",
                seconds="12",
                api_key="sk-test",
                model="sora-2",
            )

        self.assertEqual(data, b"video-bytes")
        self.assertEqual(ext, ".mp4")
        first_request_payload = json.loads(calls[0][2])
        self.assertIn("seconds", first_request_payload)
        final_video_post_payload = json.loads([c[2] for c in calls if c[0] == "https://api.openai.com/v1/videos"][-1])
        self.assertEqual(set(final_video_post_payload.keys()), {"model", "prompt"})

    def test_openai_video_downloads_content_from_video_id_when_completed(self):
        class FakeResponse:
            def __init__(self, payload=None, headers=None, data_bytes=None):
                self.payload = payload or {}
                self.headers = headers or {"Content-Type": "application/json"}
                self.data_bytes = data_bytes

            def read(self):
                if self.data_bytes is not None:
                    return self.data_bytes
                return json.dumps(self.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        calls = []

        def fake_urlopen(req, timeout=0):
            if isinstance(req, str):
                url = req
                method = "GET"
            else:
                url = req.full_url
                method = req.method
            calls.append((url, method))

            if url == "https://api.openai.com/v1/videos" and method == "POST":
                return FakeResponse({"id": "vid_123", "status": "in_progress"})
            if url == "https://api.openai.com/v1/videos/vid_123" and method == "GET":
                return FakeResponse({"id": "vid_123", "status": "completed"})
            if url == "https://api.openai.com/v1/videos/vid_123/content" and method == "GET":
                return FakeResponse(headers={"Content-Type": "video/mp4"}, data_bytes=b"video-binary")
            raise AssertionError(f"unexpected URL {url} ({method})")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), mock.patch("time.sleep"):
            data, ext = openai_video(
                prompt="a dog chasing a cat",
                size="720x1280",
                seconds="4",
                api_key="sk-test",
                model="sora-2",
            )

        self.assertEqual(data, b"video-binary")
        self.assertEqual(ext, ".mp4")
        self.assertIn(("https://api.openai.com/v1/videos/vid_123/content", "GET"), calls)

    def test_openai_video_surfaces_failed_status_message(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
                self.headers = {"Content-Type": "application/json"}

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(req, timeout=0):
            if isinstance(req, str):
                url = req
                method = "GET"
            else:
                url = req.full_url
                method = req.method
            if url == "https://api.openai.com/v1/videos" and method == "POST":
                return FakeResponse({"id": "vid_124", "status": "queued"})
            if url == "https://api.openai.com/v1/videos/vid_124" and method == "GET":
                return FakeResponse({"id": "vid_124", "status": "failed", "error": {"message": "safety policy hit"}})
            raise AssertionError(f"unexpected URL {url} ({method})")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), mock.patch("time.sleep"):
            with self.assertRaises(ValueError) as ctx:
                openai_video(
                    prompt="a dog chasing a cat",
                    size="720x1280",
                    seconds="4",
                    api_key="sk-test",
                    model="sora-2",
                )

        self.assertIn("failed", str(ctx.exception))
        self.assertIn("safety policy hit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
