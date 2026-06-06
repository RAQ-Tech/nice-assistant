import json
import re


CHAT_TITLE_PLACEHOLDERS = {"new chat", "untitled chat"}
CHAT_TITLE_MAX_LENGTH = 48
CHAT_TITLE_MIN_LENGTH = 8
CHAT_TITLE_FALLBACK_LENGTH = 40
CHAT_TITLE_OPENER_PATTERN = re.compile(
    r"^(?:hey|hi|hello|yo|good\s+(?:morning|afternoon|evening)|"
    r"can\s+you|could\s+you|would\s+you|please|i\s+need\s+help\s+with|"
    r"i\s+need\s+to|help\s+me\s+with)\b[\s,:-]*",
    re.IGNORECASE,
)


def generate_chat_title(text):
    source = "" if text is None else str(text)
    normalized = re.sub(r"\s+", " ", source).strip()
    if not normalized:
        return source[:CHAT_TITLE_FALLBACK_LENGTH]

    candidate = normalized
    while True:
        trimmed = CHAT_TITLE_OPENER_PATTERN.sub("", candidate, count=1).strip()
        if trimmed == candidate:
            break
        candidate = trimmed

    clause = re.split(r"[.!?;:\n]", candidate, maxsplit=1)[0].strip()
    if clause:
        candidate = clause

    candidate = candidate[:CHAT_TITLE_MAX_LENGTH].strip().rstrip(".,!?;:-")
    if len(candidate) < CHAT_TITLE_MIN_LENGTH:
        return normalized[:CHAT_TITLE_FALLBACK_LENGTH]
    return candidate


def generate_chat_title_from_first_user_message(text: str, max_len: int = 40) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return ""
    return normalized[:max_len]


def chat_title_needs_autogeneration(title: str | None) -> bool:
    normalized = (title or "").strip()
    if not normalized:
        return True
    return normalized.lower() in CHAT_TITLE_PLACEHOLDERS


def parse_model_options(payload_settings):
    if not isinstance(payload_settings, dict):
        return {}
    schema = {
        "temperature": float,
        "top_p": float,
        "num_predict": int,
        "presence_penalty": float,
        "frequency_penalty": float,
    }
    options = {}
    for key, caster in schema.items():
        val = payload_settings.get(key)
        if val is None or val == "":
            continue
        try:
            options[key] = caster(val)
        except (TypeError, ValueError):
            continue
    return options


def parse_traits(raw_traits):
    if not raw_traits:
        return {
            "warmth": 50,
            "creativity": 50,
            "directness": 50,
            "conversational": 50,
            "casual": 50,
            "gender": "unspecified",
            "gender_other": "",
            "age": "",
        }
    try:
        parsed = json.loads(raw_traits) if isinstance(raw_traits, str) else dict(raw_traits)
    except (TypeError, ValueError):
        parsed = {}
    return {
        "warmth": int(parsed.get("warmth", 50)),
        "creativity": int(parsed.get("creativity", 50)),
        "directness": int(parsed.get("directness", 50)),
        "conversational": int(parsed.get("conversational", 50)),
        "casual": int(parsed.get("casual", 50)),
        "gender": str(parsed.get("gender", "unspecified") or "unspecified"),
        "gender_other": str(parsed.get("gender_other", "") or ""),
        "age": str(parsed.get("age", "") or ""),
    }


def persona_instruction_block(persona_row):
    if not persona_row:
        return ""
    traits = parse_traits(persona_row["traits_json"])
    warmth = "high" if traits["warmth"] >= 67 else ("low" if traits["warmth"] <= 33 else "moderate")
    creativity = "high" if traits["creativity"] >= 67 else ("low" if traits["creativity"] <= 33 else "moderate")
    directness = "high" if traits["directness"] >= 67 else ("low" if traits["directness"] <= 33 else "moderate")
    conversational = "conversational" if traits["conversational"] >= 60 else ("informational" if traits["conversational"] <= 40 else "balanced")
    casual = "casual" if traits["casual"] >= 60 else ("professional" if traits["casual"] <= 40 else "balanced")

    lines = [
        f"You are the persona named '{persona_row['name']}'. If asked your name or identity in this chat, answer as this persona.",
        f"Tone controls: warmth={warmth} ({traits['warmth']}/100), creativity={creativity} ({traits['creativity']}/100), directness={directness} ({traits['directness']}/100).",
        f"Style controls: conversational_vs_informational={conversational} ({traits['conversational']}/100), casual_vs_professional={casual} ({traits['casual']}/100).",
    ]
    if traits["gender"] == "other" and traits["gender_other"]:
        lines.append(f"Persona gender: {traits['gender_other']}")
    elif traits["gender"] != "unspecified":
        lines.append(f"Persona gender: {traits['gender']}")
    if traits["age"]:
        lines.append(f"Persona age: {traits['age']}")
    if persona_row["personality_details"]:
        lines.append(f"Persona details: {persona_row['personality_details']}")
    if persona_row["system_prompt"]:
        lines.append(persona_row["system_prompt"])
    return "\n".join(lines)


def looks_like_image_request(text):
    if not text:
        return False
    lowered = " ".join(text.lower().split())
    verbs = ("generate", "create", "make", "draw", "render")
    nouns = ("image", "picture", "photo", "illustration", "art")
    has_verb = any(v in lowered for v in verbs)
    has_noun = any(n in lowered for n in nouns)
    return has_verb and has_noun


def looks_like_video_request(text):
    if not text:
        return False
    lowered = " ".join(text.lower().split())
    verbs = ("generate", "create", "make", "render", "produce")
    nouns = ("video", "clip", "animation", "movie", "footage")
    has_verb = any(v in lowered for v in verbs)
    has_noun = any(n in lowered for n in nouns)
    return has_verb and has_noun
