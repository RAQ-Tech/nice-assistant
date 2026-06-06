import json
import re
import secrets
import urllib.error
import urllib.parse

from app.chat import parse_traits


IMAGE_QUALITY_ALIASES = {
    "standard": "medium",
    "hd": "high",
}
IMAGE_QUALITY_VALUES = {"low", "medium", "high", "auto", "none"}
SUPPORTED_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
SUPPORTED_VIDEO_MODELS = {"sora-2", "sora-2-pro"}
SUPPORTED_VIDEO_SECONDS = {"4", "8", "12"}
SUPPORTED_VIDEO_SIZES = {"720x1280", "1280x720", "1024x1792", "1792x1024"}
SUPPORTED_VIDEO_SIZES_BY_MODEL = {
    "sora-2": {"720x1280", "1280x720"},
    "sora-2-pro": SUPPORTED_VIDEO_SIZES,
}
MODEL_IMAGE_TAG_PATTERN = re.compile(r"<generate_image>(.*?)</generate_image>", re.IGNORECASE | re.DOTALL)
OPENAI_IMAGE_TERM_REPLACEMENTS = {
    "nsfw": "safe-for-work",
    "nude": "fully clothed",
    "naked": "fully clothed",
    "explicit sex": "romantic scene",
    "sexual": "romantic",
    "porn": "editorial",
    "fetish": "fashion concept",
    "gore": "dramatic",
    "graphic violence": "intense action",
}


def normalize_video_model(model):
    candidate = (model or "").strip().lower()
    if candidate in SUPPORTED_VIDEO_MODELS:
        return candidate
    return "sora-2"


def normalize_video_seconds(seconds):
    candidate = str(seconds or "").strip()
    if candidate in SUPPORTED_VIDEO_SECONDS:
        return candidate
    return "4"


def normalize_video_size(size, model="sora-2"):
    candidate = (size or "").strip().lower()
    normalized_model = normalize_video_model(model)
    allowed_sizes = SUPPORTED_VIDEO_SIZES_BY_MODEL.get(normalized_model, SUPPORTED_VIDEO_SIZES_BY_MODEL["sora-2"])
    if candidate in allowed_sizes:
        return candidate
    if normalized_model == "sora-2-pro":
        return "1024x1792"
    return "720x1280"


def normalize_local_image_base_url(base_url, default_base_url):
    candidate = (base_url or "").strip() or default_base_url
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local image server URL must be a valid http(s) URL")
    return candidate.rstrip("/")


def normalize_local_image_backend(value):
    candidate = (value or "").strip().lower()
    if candidate in {"automatic1111", "comfyui"}:
        return candidate
    return "automatic1111"


def _coerce_number(value, default, cast_type=float):
    try:
        return cast_type(value)
    except (TypeError, ValueError):
        return default


def parse_additional_parameters(raw):
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError("Additional Parameters must be valid JSON object text")
    if not isinstance(parsed, dict):
        raise ValueError("Additional Parameters must be a JSON object")
    return parsed


def normalize_image_quality(quality):
    normalized = IMAGE_QUALITY_ALIASES.get(quality, quality)
    if normalized in IMAGE_QUALITY_VALUES:
        return normalized
    return "auto"


def normalize_openai_image_quality(quality):
    normalized = normalize_image_quality(quality)
    if normalized == "none":
        return "auto"
    return normalized


def normalize_image_size(size):
    if size in SUPPORTED_IMAGE_SIZES:
        return size
    return "1024x1024"


def parse_image_size(size, allow_custom=False):
    raw = (size or "").strip().lower()
    if allow_custom and raw:
        custom_match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", raw)
        if custom_match:
            return int(custom_match.group(1)), int(custom_match.group(2))
    normalized = normalize_image_size(raw)
    if normalized == "auto":
        return 1024, 1024
    try:
        width, height = normalized.split("x", 1)
        return int(width), int(height)
    except Exception:
        return 1024, 1024


def clean_user_image_prompt(prompt):
    text = " ".join((prompt or "").split()).strip()
    if not text:
        return ""
    prefixes = [
        r"^(please\s+)?(can you\s+)?(generate|create|make|draw|render|produce)\s+(me\s+)?(an?|the)?\s*(image|picture|photo|illustration|artwork)?\s*(of|with)?\s+",
        r"^(please\s+)?(show|give)\s+me\s+(an?|the)?\s*(image|picture|photo|illustration)\s*(of|with)?\s+",
    ]
    cleaned = text
    for pattern in prefixes:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^an?\s+image\s+of\s+", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^(the\s+following\s+prompt\s*:?\s*)", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    cleaned = re.sub(r"^(prompt\s*:?\s*)", "", cleaned, flags=re.IGNORECASE).strip(" ,.:;-")
    return cleaned or text


def adjust_prompt_for_openai_image(prompt):
    text = clean_user_image_prompt(prompt)
    if not text:
        return "Generate a polished, policy-compliant image suitable for general audiences."
    adjusted = text
    for term, replacement in OPENAI_IMAGE_TERM_REPLACEMENTS.items():
        adjusted = re.sub(rf"\b{re.escape(term)}\b", replacement, adjusted, flags=re.IGNORECASE)
    return (
        "Generate a polished, coherent image with clear subject emphasis, intentional composition, and rich lighting detail. "
        f"Scene request: {adjusted}. "
        "Keep it policy-compliant and suitable for general audiences."
    )


def local_steps_from_quality(quality):
    normalized = normalize_image_quality(quality)
    if normalized == "high":
        return 38
    if normalized == "low":
        return 20
    return 28


def local_negative_prompt(allow_nsfw, quality="auto"):
    if normalize_image_quality(quality) == "none":
        return ""
    base = "blurry, lowres, jpeg artifacts, extra limbs, deformed hands, bad anatomy, watermark, text, logo"
    if allow_nsfw:
        return base
    return f"{base}, nude, nudity, explicit sexual content, fetish, porn, graphic violence, gore"


def adjust_prompt_for_local_sd(prompt, allow_nsfw, quality="auto"):
    text = clean_user_image_prompt(prompt)
    if not text:
        text = "cinematic portrait of a friendly assistant in a modern workspace, detailed lighting, highly detailed"
    if not allow_nsfw:
        for term, replacement in OPENAI_IMAGE_TERM_REPLACEMENTS.items():
            text = re.sub(rf"\b{re.escape(term)}\b", replacement, text, flags=re.IGNORECASE)
    if normalize_image_quality(quality) == "none":
        return text
    return f"masterpiece, best quality, highly detailed, {text}"


def local_seed_for_backend(seed_value, backend):
    normalized_backend = normalize_local_image_backend(backend)
    if normalized_backend == "comfyui":
        seed_raw = str(seed_value or "").strip()
        if seed_raw in {"", "-1"}:
            return secrets.randbelow(2**63 - 1) + 1
        return int(_coerce_number(seed_value, secrets.randbelow(2**63 - 1) + 1, int))
    return int(_coerce_number(seed_value, -1, int))


def image_prompt_is_detailed(prompt):
    words = re.findall(r"[A-Za-z0-9']+", prompt or "")
    if len(words) < 12:
        return False
    checks = [
        re.search(r"\b(shot|close-up|wide|angle|composition|framing|portrait|landscape)\b", prompt, re.IGNORECASE),
        re.search(r"\b(light|lighting|sunset|neon|moody|dramatic|soft light)\b", prompt, re.IGNORECASE),
        re.search(r"\b(style|illustration|photo|cinematic|render|painting|anime|realistic)\b", prompt, re.IGNORECASE),
    ]
    return sum(bool(c) for c in checks) >= 1


def model_image_instruction_for_provider(provider, local_backend="automatic1111"):
    provider = (provider or "disabled").lower().strip()
    base = (
        "When a user clearly asks for an image, include exactly one XML tag like "
        "<generate_image>...</generate_image> in your reply. "
        "Inside the tag, provide a production-quality prompt with subject, environment, composition/camera, "
        "lighting, style, and quality details. Keep it safe and policy-compliant. "
        "If the image includes the user or assistant persona, preserve known visual continuity from chat memory/persona settings "
        "(for example avatar cues, physical traits, and wardrobe style) and do not invent sensitive physical details that are unknown."
    )
    if provider == "openai":
        return (
            f"{base} Tailor prompt style for OpenAI image generation: natural-language scene direction, clear visual intent, "
            "minimal comma-stuffing, and no tool-specific weight syntax."
        )
    if provider == "local":
        backend = normalize_local_image_backend(local_backend)
        if backend == "comfyui":
            return (
                f"{base} Tailor prompt style for ComfyUI/Stable Diffusion pipelines: concise keyword-rich descriptors, "
                "clear art direction tokens, and maintain compatibility with a separate negative prompt/workflow nodes."
            )
        return (
            f"{base} Tailor prompt style for Stable Diffusion/Automatic1111: concise keyword-rich descriptors, "
            "art direction tokens, and include details that pair well with a separate negative prompt."
        )
    return base


def visual_identity_context(conn, uid, chat_id, persona_row, workspace_id=None, persona_id=None):
    cues = []
    if persona_row:
        traits = parse_traits(persona_row["traits_json"])
        persona_bits = [f"assistant persona is '{persona_row['name']}'"]
        if traits["gender"] == "other" and traits["gender_other"]:
            persona_bits.append(f"gender: {traits['gender_other']}")
        elif traits["gender"] != "unspecified":
            persona_bits.append(f"gender: {traits['gender']}")
        if traits["age"]:
            persona_bits.append(f"age: {traits['age']}")
        if persona_row["personality_details"]:
            persona_bits.append(f"persona profile: {persona_row['personality_details'][:180]}")
        cues.append("; ".join(persona_bits))

    if conn and uid:
        rows = []
        if chat_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='chat' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, chat_id),
                ).fetchall()
            )
        if persona_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='persona' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, persona_id),
                ).fetchall()
            )
        if workspace_id:
            rows.extend(
                conn.execute(
                    "SELECT content FROM memories WHERE user_id=? AND tier='workspace' AND tier_ref_id=? ORDER BY created_at DESC LIMIT 20",
                    (uid, workspace_id),
                ).fetchall()
            )
        if chat_id:
            chat_rows = conn.execute(
                "SELECT text AS content FROM messages WHERE chat_id=? ORDER BY created_at DESC LIMIT 20",
                (chat_id,),
            ).fetchall()
            rows.extend(chat_rows)
        pat = re.compile(r"\b(my|i am|i'm|look like|appearance|hair|eyes|face|skin|height|wear|wearing|outfit|avatar)\b", re.IGNORECASE)
        seen = set()
        for row in rows:
            content = " ".join(str(row[0] or "").split())
            if len(content) < 8 or not pat.search(content):
                continue
            lowered = content.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cues.append(content[:180])
            if len(cues) >= 6:
                break

    if not cues:
        return ""
    return (
        "Visual continuity constraints: "
        + " | ".join(cues)
        + " If details are missing, keep identity descriptors neutral instead of guessing."
    )


def _extract_http_error_detail(exc):
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
        if not body:
            return ""
        parsed = json.loads(body)
        return ((parsed.get("error") or {}).get("message") or parsed.get("message") or body).strip()
    except Exception:
        try:
            return body.strip()
        except Exception:
            return ""


def user_safe_image_error(exc, provider="openai"):
    provider = (provider or "openai").strip().lower()
    detail = _extract_http_error_detail(exc)
    req_match = re.search(r"(req_[a-zA-Z0-9]+)", detail or "")
    req_id = req_match.group(1) if req_match else ""
    if "safety" in (detail or "").lower() or "safety_violations" in (detail or "").lower():
        return "I couldn't generate that image because the request was flagged by safety filters. Please try a safer rewording.", detail, req_id
    if provider == "openai" and "Supported values are" in (detail or "") and "Invalid value" in (detail or ""):
        return "That image size is not supported by OpenAI. Please choose 1024x1024, 1024x1536, 1536x1024, or auto.", detail, req_id
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if provider in {"local", "local/automatic1111"}:
            if status in (401, 403):
                return "Image generation failed: Automatic1111 rejected authentication. Check the API auth string and --api-auth setup.", detail, req_id
            if status == 404:
                return "Automatic1111 API endpoint not found. Ensure webui is running with the --api flag.", detail, req_id
            if status == 422:
                return "Automatic1111 rejected one or more generation parameters. Check image size/steps/sampler/scheduler values.", detail, req_id
            if 500 <= status <= 599:
                return "Automatic1111 failed while generating the image. Please retry after the server is ready.", detail, req_id
            return f"Image generation failed with Automatic1111 HTTP {status}.", detail, req_id
        if provider == "local/comfyui":
            if status in (401, 403):
                return "Image generation failed: ComfyUI rejected authentication. Check your API auth settings.", detail, req_id
            if status == 404:
                return "ComfyUI route not found. Verify the ComfyUI server URL and that the server is running.", detail, req_id
            if status == 422:
                return "ComfyUI rejected one or more workflow parameters. Check model, sampler/scheduler, and additional JSON.", detail, req_id
            if 500 <= status <= 599:
                return "ComfyUI failed while generating the image. Please retry after the server is ready.", detail, req_id
            return f"Image generation failed with ComfyUI HTTP {status}.", detail, req_id
        if status in (401, 403):
            return "Image generation failed: OpenAI rejected the request (check API key and image model access).", detail, req_id
        if status == 429:
            return "Image generation is rate limited by OpenAI right now. Please retry in a moment.", detail, req_id
        if status in (400, 404):
            return "OpenAI couldn't generate that image from the current request. Please revise the prompt and try again.", detail, req_id
        if 500 <= status <= 599:
            return "Image generation is temporarily unavailable from OpenAI. Please try again shortly.", detail, req_id
        return f"Image generation failed with OpenAI error HTTP {status}.", detail, req_id
    if isinstance(exc, urllib.error.URLError):
        if provider in {"local", "local/automatic1111"}:
            return "Image generation is currently unavailable because Automatic1111 could not be reached. Check URL and that webui is running with --api.", detail, req_id
        if provider == "local/comfyui":
            return "Image generation is currently unavailable because ComfyUI could not be reached. Check URL and that the ComfyUI server is running.", detail, req_id
        return "Image generation is currently unavailable because the image provider could not be reached. Please try again in a moment.", detail, req_id
    if isinstance(exc, TimeoutError):
        return "Image generation timed out. Please try again with a shorter prompt or retry shortly.", detail, req_id
    return "Image generation failed unexpectedly. Please try again.", detail, req_id


def user_safe_video_error(exc):
    detail = _extract_http_error_detail(exc)
    req_match = re.search(r"(req_[a-zA-Z0-9]+)", detail or "")
    req_id = req_match.group(1) if req_match else ""
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if status in (401, 403):
            return "Video generation failed: OpenAI rejected the request (check API key and video model access).", detail, req_id
        if status == 429:
            return "Video generation is rate limited by OpenAI right now. Please retry in a moment.", detail, req_id
        if status in (400, 404, 422):
            if detail:
                return (
                    "OpenAI rejected the video request settings or payload. "
                    "Try video model 'sora-2' with 4 seconds and size 720x1280 or 1280x720, then retry."
                ), detail, req_id
            return "OpenAI couldn't generate that video from the current request. Please revise the prompt and try again.", detail, req_id
        if 500 <= status <= 599:
            return "Video generation is temporarily unavailable from OpenAI. Please try again shortly.", detail, req_id
        return f"Video generation failed with OpenAI error HTTP {status}.", detail, req_id
    if isinstance(exc, urllib.error.URLError):
        return "Video generation is currently unavailable because the OpenAI provider could not be reached. Please try again in a moment.", detail, req_id
    if isinstance(exc, TimeoutError):
        return "Video generation timed out. Please try again with a shorter prompt or retry shortly.", detail, req_id
    return "Video generation failed unexpectedly. Please try again.", detail, req_id


def extract_model_image_prompt(reply_text):
    if not reply_text:
        return "", ""
    match = MODEL_IMAGE_TAG_PATTERN.search(reply_text)
    if not match:
        return reply_text, ""
    prompt = " ".join(match.group(1).split()).strip()
    clean_reply = MODEL_IMAGE_TAG_PATTERN.sub("", reply_text).strip()
    return clean_reply, prompt
