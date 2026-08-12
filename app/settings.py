import json

from app.auth import mask_secret
from app.media import (
    IMAGE_QUALITY_ALIASES,
    IMAGE_QUALITY_VALUES,
    SUPPORTED_IMAGE_SIZES,
    SUPPORTED_VIDEO_MODELS,
    SUPPORTED_VIDEO_SECONDS,
    SUPPORTED_VIDEO_SIZES_BY_MODEL,
)
from app.service_errors import RequestError


def settings_for_response(row):
    if not row:
        return {
            "default_memory_mode": "saved",
            "stt_provider": "disabled",
            "tts_provider": "disabled",
            "tts_format": "wav",
            "openai_api_key": "",
            "preferences_json": "{}",
        }
    data = dict(row)
    data["openai_api_key"] = mask_secret(data.get("openai_api_key"))
    return data


def truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_preferences_json(raw_value):
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def normalize_media_preferences(value):
    """Canonicalize persisted media preferences and human-experience defaults."""

    preferences = dict(value or {}) if isinstance(value, dict) else {}
    # Image consent is now expressed by the user's explicit request and the
    # selected persona's durable permission. Ignore the retired per-image
    # confirmation preference when an older browser submits it.
    preferences.pop("image_confirmation_policy", None)
    preferences["chat_blur_images"] = truthy(preferences.get("chat_blur_images", False))
    if "image_provider" in preferences:
        provider = str(preferences.get("image_provider") or "disabled").strip().lower()
        if provider == "local/automatic1111":
            preferences["image_provider"] = "local"
            preferences["image_local_backend"] = "automatic1111"
        elif provider == "local/comfyui":
            preferences["image_provider"] = "local"
            preferences["image_local_backend"] = "comfyui"
        elif provider in {"disabled", "local", "openai"}:
            preferences["image_provider"] = provider
    if "image_local_backend" in preferences:
        backend = str(preferences.get("image_local_backend") or "").strip().lower()
        if backend in {"automatic1111", "comfyui"}:
            preferences["image_local_backend"] = backend
    return preferences


def _accepted_video_sizes() -> set[str]:
    return {size for sizes in SUPPORTED_VIDEO_SIZES_BY_MODEL.values() for size in sizes}


# Values the media runtime actually recognizes. Anything else is replaced with a default at
# generation time, so accepting it here would store a choice the deployment never honors.
MEDIA_PREFERENCE_CHOICES: dict[str, set[str]] = {
    "image_provider": {"disabled", "local", "openai", "local/automatic1111", "local/comfyui"},
    "image_local_backend": {"automatic1111", "comfyui"},
    "image_size": set(SUPPORTED_IMAGE_SIZES),
    "image_quality": set(IMAGE_QUALITY_VALUES) | set(IMAGE_QUALITY_ALIASES),
    "video_provider": {"disabled", "openai"},
    "video_model": set(SUPPORTED_VIDEO_MODELS),
    "video_size": _accepted_video_sizes(),
    "video_duration": set(SUPPORTED_VIDEO_SECONDS),
}


def validate_media_preferences(preferences: dict, previous: dict | None = None) -> None:
    """Reject media choices the runtime cannot honor.

    Only submitted values that differ from what is already stored are checked. An account
    that predates this validation keeps whatever it holds and can still save its other
    settings; the browser resubmits every stored value on each save, so validating an
    unchanged one would leave that account unable to save anything at all.
    """

    previous = previous if isinstance(previous, dict) else {}
    for key, accepted in MEDIA_PREFERENCE_CHOICES.items():
        if key not in preferences:
            continue
        raw = preferences[key]
        if raw is None or str(raw).strip() == "":
            continue
        if str(raw) == str(previous.get(key, "")):
            continue
        if str(raw).strip().lower() in accepted:
            continue
        allowed = ", ".join(sorted(accepted))
        raise RequestError(
            f"{key} must be one of: {allowed}. The deployment cannot honor '{raw}', "
            "and saving it would silently produce something else.",
            422,
        )


def setting_bool(settings_row, key, default=False):
    prefs = parse_preferences_json(settings_row["preferences_json"] if settings_row else "{}")
    val = prefs.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)
