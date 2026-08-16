"""One idea, several frames.

A photo set is a shared scene plus the things that change between frames. The
split matters: wardrobe, room, and lighting belong to the set, and pose, angle,
and framing belong to the frame. Anything shared is stated once, so it cannot
drift between frames the way it does when the same scene is described several
times and generated separately.

The seed relationship is recorded rather than incidental. Frame `n` uses
`base_seed + n`, which keeps the frames related without making them identical,
and means a set can be reproduced later from two numbers instead of from luck.
"""

from __future__ import annotations

from app.media_scene import SCENE_FIELDS, normalize_scene, scene_is_empty


# What a frame is allowed to change. Everything else is the set's, and stating
# it per frame would be the drift this exists to prevent.
FRAME_FIELDS = ("action", "framing", "camera", "mood")
MAX_FRAMES = 12
MIN_FRAMES = 2
SET_STATES = ("planned", "generating", "done", "partial", "retired")


def normalize_variations(values) -> list[dict]:
    """Clean the per-frame differences, dropping anything a frame may not set.

    A variation that tries to change wardrobe or setting is not rejected: the
    disallowed fields are simply removed, because the useful part of the request
    is usually the pose, and refusing the whole frame over an extra field would
    lose it.
    """

    variations = []
    for item in list(values or [])[:MAX_FRAMES]:
        if not isinstance(item, dict):
            continue
        variation = {}
        for field in FRAME_FIELDS:
            text = " ".join(str(item.get(field) or "").split()).strip()
            if text:
                variation[field] = text[:400]
        variations.append(variation)
    return variations


def frame_scene(base: dict, variation: dict) -> dict:
    """The scene for one frame: the set's, with this frame's changes applied."""

    scene = normalize_scene(base)
    for field in FRAME_FIELDS:
        value = str((variation or {}).get(field) or "").strip()
        if value:
            scene[field] = value
    return normalize_scene(scene)


def frame_seed(base_seed: int, index: int) -> int:
    """Frame `index` of a set with this base seed.

    Deliberately arithmetic rather than random. Two numbers reproduce the whole
    set, and a frame that needs regenerating comes back the same.
    """

    return int(base_seed) + int(index)


def normalize_definition(values) -> dict:
    """Validate a set, or say what is wrong with it.

    Returns the normalized definition and the reasons it cannot be used. An
    empty reason list means it can be produced.
    """

    scene = normalize_scene((values or {}).get("scene"))
    variations = normalize_variations((values or {}).get("variations"))
    reasons = []
    if scene_is_empty(scene):
        reasons.append("the set needs a scene shared by every frame")
    if len(variations) < MIN_FRAMES:
        reasons.append(f"a set needs at least {MIN_FRAMES} frames; one frame is just a picture")
    if variations and not any(variation for variation in variations):
        # Every frame identical is not a set, it is the same picture repeated,
        # and the seed offset alone is not a described difference.
        reasons.append("frames must differ by pose, angle, framing, or mood")
    return {
        "scene": scene,
        "variations": variations,
        "frame_count": len(variations),
        "reasons": reasons,
    }


def shared_summary(scene: dict) -> str:
    """What every frame in this set has in common, for the journal and the UI."""

    scene = normalize_scene(scene)
    parts = [scene.get(field, "") for field in SCENE_FIELDS if field not in FRAME_FIELDS]
    return ", ".join(part for part in parts if part)


def set_state(frames_total: int, frames_done: int, *, finished: bool) -> str:
    """What a set should say about itself.

    `partial` exists because a set that made four of six frames is neither done
    nor still working, and calling it either one is a lie somebody would act on.
    """

    if frames_done >= frames_total and frames_total:
        return "done"
    if not finished:
        return "generating"
    return "partial" if frames_done else "planned"
