import json

from app.auth import mask_secret


def settings_for_response(row):
    if not row:
        return {
            "default_memory_mode": "auto",
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


def parse_residency_policy_preferences(prefs):
    policy = {}
    if not isinstance(prefs, dict):
        return policy
    mapping = {
        "gpu_idle_hold_seconds_llm": float,
        "gpu_idle_hold_seconds_image": float,
        "gpu_min_residency_seconds": float,
        "max_model_swaps_per_minute": int,
        "queue_affinity_window_ms": int,
    }
    for key, caster in mapping.items():
        value = prefs.get(key)
        if value is None or value == "":
            continue
        try:
            converted = caster(value)
        except (TypeError, ValueError):
            continue
        if caster is float:
            policy[key] = max(0.0, converted)
        else:
            policy[key] = max(0, converted)
    return policy


def setting_bool(settings_row, key, default=False):
    prefs = parse_preferences_json(settings_row["preferences_json"] if settings_row else "{}")
    val = prefs.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)
