"""The typed scene a Task Model emits instead of finished prompt text.

Prompt syntax belongs to the checkpoint, not to the request, so asking a small
local model to write a prompt means asking it to make a decision it has no basis
for - and to make it consistently, in whichever dialect happens to apply. A
scene records what the picture is *of*; rendering it into booru tags or prose is
the compiler's job.

It also makes variation cheap: permuting one field of a scene is a coherent new
picture, which is what a pre-generated library needs.
"""

from __future__ import annotations


# Ordered deliberately. Rendering follows this order, so a scene reads
# subject-first in prose and tags in a stable sequence for booru dialects.
SCENE_FIELDS = (
    "subject",
    "action",
    "setting",
    "wardrobe",
    "framing",
    "lighting",
    "camera",
    "mood",
)
MAX_FIELD_CHARACTERS = 200
EMPTY_SCENE = {field: "" for field in SCENE_FIELDS}


def normalize_scene(values) -> dict:
    """Return a scene with every field present, trimmed, and bounded."""

    if not isinstance(values, dict):
        return dict(EMPTY_SCENE)
    scene = {}
    for field in SCENE_FIELDS:
        text = " ".join(str(values.get(field) or "").split()).strip()
        scene[field] = text[:MAX_FIELD_CHARACTERS]
    return scene


def scene_is_empty(scene) -> bool:
    return not any((scene or {}).get(field) for field in SCENE_FIELDS)


def render_scene(scene, style: str = "natural_language") -> str:
    """Render a scene into one dialect's shape.

    Both styles are comma-separated because every current image model reads
    commas as concept separators. The difference is what the compiler wraps
    around this, which is the dialect's business, not the scene's.
    """

    parts = [(scene or {}).get(field, "") for field in SCENE_FIELDS]
    return ", ".join(part for part in parts if part)


def scene_summary(scene) -> str:
    """A short human line for the capability request and the confirmation card.

    The subject and what it is doing are what a person recognises; the rest is
    detail that belongs in the compiled prompt, not in a one-line label.
    """

    scene = scene or {}
    lead = ", ".join(part for part in (scene.get("subject"), scene.get("action")) if part)
    setting = scene.get("setting")
    if lead and setting:
        return f"{lead} in {setting}"
    return lead or setting or ""


def scene_schema() -> dict:
    """The JSON schema fragment a Task Model fills in.

    Bounds are kept small for the same reason the prompt bound is: Ollama's
    grammar compiler can fail on an otherwise valid schema with large string
    bounds before inference even starts.
    """

    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(SCENE_FIELDS),
        "properties": {field: {"type": "string", "maxLength": MAX_FIELD_CHARACTERS} for field in SCENE_FIELDS},
    }
