"""Per-model prompt dialect and the deterministic compiler that applies it.

Prompt syntax is a property of the checkpoint, not of the request. Some model
families want booru or score tags, some are damaged by quality boilerplate, and
some support no negative prompt at all. Before this, one hardcoded quality
prefix and one global negative string were applied to every local generation,
which is wrong for most of those families.

Compilation is pure: the same intent and dialect always produce the same text.
That is what makes a compiled prompt worth recording in a generation journal -
an operator can read what was sent and reproduce it exactly.
"""

from __future__ import annotations

from app.media import clean_user_image_prompt
from app.service_errors import RequestError


PROMPT_STYLES = ("natural_language", "booru", "hybrid")
TRIGGER_PLACEMENTS = ("prefix", "suffix")
MAX_DIALECT_TEXT = 2_000
MAX_TARGET_LENGTH = 20_000

# Reproduces the behavior that used to be compiled into the code, so a model
# configured before dialects existed keeps producing exactly what it produced
# before. It is a starting point to edit, not a recommendation for every model.
LEGACY_QUALITY_PREFIX = "masterpiece, best quality, highly detailed"
LEGACY_NEGATIVE = "blurry, lowres, jpeg artifacts, extra limbs, deformed hands, bad anatomy, watermark, text, logo"
# Applied by the platform when NSFW output is disabled. Kept separate from the
# model's own negative so an operator editing one never silently weakens the
# other.
SAFETY_NEGATIVE = "nude, nudity, explicit sexual content, fetish, porn, graphic violence, gore"

DEFAULT_DIALECT = {
    "style": "natural_language",
    "prefix": LEGACY_QUALITY_PREFIX,
    "suffix": "",
    "negative_prompt": LEGACY_NEGATIVE,
    "supports_negative": True,
    "trigger_placement": "suffix",
    "target_length": 0,
}


def _text(value, label: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > MAX_DIALECT_TEXT:
        raise RequestError(f"prompt dialect {label} is too long", 400)
    return text


def normalize_dialect(values) -> dict:
    """Validate an operator-supplied dialect, filling anything unstated."""

    if values is None:
        return dict(DEFAULT_DIALECT)
    if not isinstance(values, dict):
        raise RequestError("prompt dialect must be an object", 400)
    unknown = set(values) - set(DEFAULT_DIALECT)
    if unknown:
        raise RequestError(f"prompt dialect includes unsupported fields: {', '.join(sorted(unknown))}", 400)
    style = str(values.get("style") or DEFAULT_DIALECT["style"]).strip()
    if style not in PROMPT_STYLES:
        raise RequestError(f"prompt dialect style must be one of {', '.join(PROMPT_STYLES)}", 400)
    placement = str(values.get("trigger_placement") or DEFAULT_DIALECT["trigger_placement"]).strip()
    if placement not in TRIGGER_PLACEMENTS:
        raise RequestError(f"prompt dialect trigger placement must be one of {', '.join(TRIGGER_PLACEMENTS)}", 400)
    supports_negative = values.get("supports_negative", True)
    if not isinstance(supports_negative, bool):
        raise RequestError("prompt dialect supports_negative must be true or false", 400)
    try:
        target_length = int(values.get("target_length") or 0)
    except (TypeError, ValueError) as exc:
        raise RequestError("prompt dialect target length is invalid", 400) from exc
    if not 0 <= target_length <= MAX_TARGET_LENGTH:
        raise RequestError(f"prompt dialect target length must be between 0 and {MAX_TARGET_LENGTH}", 400)
    return {
        "style": style,
        "prefix": _text(values.get("prefix", DEFAULT_DIALECT["prefix"]), "prefix"),
        "suffix": _text(values.get("suffix", DEFAULT_DIALECT["suffix"]), "suffix"),
        "negative_prompt": _text(values.get("negative_prompt", DEFAULT_DIALECT["negative_prompt"]), "negative prompt"),
        "supports_negative": supports_negative,
        "trigger_placement": placement,
        "target_length": target_length,
    }


def _trigger_words(loras) -> list[str]:
    words = []
    for item in loras or []:
        if not isinstance(item, dict):
            continue
        for word in item.get("trigger_words") or []:
            cleaned = " ".join(str(word).split()).strip()
            if cleaned and cleaned not in words:
                words.append(cleaned)
    return words


def _join(parts, style: str) -> str:
    values = [part for part in parts if part]
    if not values:
        return ""
    if style == "natural_language":
        # Prose reads better when the boilerplate is comma-separated from the
        # sentence but the sentence itself is left exactly as written.
        return ", ".join(values)
    return ", ".join(values)


def _truncate(text: str, target_length: int) -> str:
    if not target_length or len(text) <= target_length:
        return text
    clipped = text[:target_length]
    boundary = clipped.rfind(",")
    # Cutting mid-tag would send a fragment the model reads as a different
    # concept, so fall back to a comma boundary when there is one.
    return (clipped[:boundary] if boundary > 0 else clipped).strip().strip(",")


def compile_prompt(intent: str, dialect=None, *, loras=(), allow_nsfw: bool = True) -> dict:
    """Render a request into one model's dialect.

    Returns the exact positive and negative text to submit, plus the decisions
    taken, so a journal can show why the submitted text differs from the
    request.
    """

    resolved = dialect if isinstance(dialect, dict) and dialect else dict(DEFAULT_DIALECT)
    style = resolved.get("style") or "natural_language"
    text = clean_user_image_prompt(intent)
    triggers = _trigger_words(loras)
    placement = resolved.get("trigger_placement") or "suffix"
    parts = [resolved.get("prefix") or ""]
    if placement == "prefix":
        parts.extend(triggers)
    parts.append(text)
    if placement == "suffix":
        parts.extend(triggers)
    parts.append(resolved.get("suffix") or "")
    positive = _join(parts, style)
    target_length = int(resolved.get("target_length") or 0)
    truncated = bool(target_length and len(positive) > target_length)
    positive = _truncate(positive, target_length)

    supports_negative = bool(resolved.get("supports_negative", True))
    if supports_negative:
        negative_parts = [resolved.get("negative_prompt") or ""]
        if not allow_nsfw:
            negative_parts.append(SAFETY_NEGATIVE)
        negative = _join(negative_parts, style)
    else:
        negative = ""
    return {
        "positive": positive,
        "negative": negative,
        "style": style,
        "trigger_words": triggers,
        "truncated": truncated,
        "supports_negative": supports_negative,
        # Stated because a model that takes no negative prompt cannot carry the
        # platform's safety negative either, and that should never be implied.
        "safety_negative_applied": bool(supports_negative and not allow_nsfw),
    }
