"""Suggested starting settings for a checkpoint, from evidence, not guesses.

A checkpoint file can carry a small metadata block naming its architecture;
ComfyUI serves that block over HTTP when it knows how. When the block is
missing, the filename often still names the family. Both are only evidence,
so every suggestion carries its ``source`` and the page presents it as a
starting point to review, never as a measurement.

The family table is the single home for these numbers. The browser renders
whatever this module says; it holds no copy of its own.
"""

from __future__ import annotations

# Typical published starting points per architecture family. Values are what
# each family's documentation and reference workflows commonly use - a place
# to start, deliberately not tuned to any one deployment.
FAMILY_DEFAULTS: dict[str, dict] = {
    "sdxl": {
        "label": "SDXL",
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "cfg_scale": 6.0,
        "prompt_style": "natural_language",
    },
    "sd15": {
        "label": "SD 1.5",
        "width": 512,
        "height": 512,
        "steps": 25,
        "cfg_scale": 7.0,
        "prompt_style": "natural_language",
    },
    "flux": {
        "label": "Flux",
        "width": 1024,
        "height": 1024,
        "steps": 20,
        "cfg_scale": 1.0,
        "prompt_style": "natural_language",
    },
    # De-distilled Flux lineage: real CFG again, so Flux's 1.0 would be
    # actively wrong for it.
    "chroma": {
        "label": "Chroma",
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "cfg_scale": 4.0,
        "prompt_style": "natural_language",
    },
}

# Architecture strings seen in safetensors metadata, longest match wins.
_ARCHITECTURE_FAMILIES = (
    ("chroma", "chroma"),
    ("stable-diffusion-xl", "sdxl"),
    ("sdxl", "sdxl"),
    ("stable-diffusion-v1", "sd15"),
    ("sd-v1", "sd15"),
    ("sd_v1", "sd15"),
    ("flux", "flux"),
)

# Filename fragments that name a family clearly enough to suggest from.
_FILENAME_FAMILIES = (
    ("chroma", "chroma"),
    ("flux", "flux"),
    ("sdxl", "sdxl"),
    ("xl", "sdxl"),
    ("pony", "sdxl"),
    ("illustrious", "sdxl"),
    ("sd15", "sd15"),
    ("sd-1-5", "sd15"),
    ("v1-5", "sd15"),
    ("1.5", "sd15"),
)


def family_from_metadata(metadata: dict) -> str | None:
    """The family a file's own metadata block declares, if any."""

    declared = " ".join(
        str(metadata.get(key, "")) for key in ("modelspec.architecture", "ss_base_model_version", "architecture")
    ).lower()
    for fragment, family in _ARCHITECTURE_FAMILIES:
        if fragment in declared:
            return family
    return None


def family_from_filename(filename: str) -> str | None:
    """The family a filename suggests. A guess, and labeled as one upstream."""

    lowered = filename.lower()
    for fragment, family in _FILENAME_FAMILIES:
        if fragment in lowered:
            return family
    return None


def prefill_suggestions(checkpoint: str, metadata: dict | None) -> dict:
    """Suggested settings with their provenance attached.

    ``source`` is ``file`` when the file's metadata named the family,
    ``filename`` when only the name did, and ``none`` when neither says
    anything worth suggesting from.
    """

    family = family_from_metadata(metadata) if metadata else None
    source = "file" if family else None
    if not family:
        family = family_from_filename(checkpoint)
        source = "filename" if family else None
    if not family or family not in FAMILY_DEFAULTS:
        return {"ok": True, "source": "none", "family": None, "message": "The file does not say what family it is."}
    defaults = FAMILY_DEFAULTS[family]
    title = str((metadata or {}).get("modelspec.title") or "").strip()
    origin = (
        f"read from the file: this is an {defaults['label']} model"
        if source == "file"
        else f"guessed from the file name, which looks like {defaults['label']} — check it"
    )
    return {
        "ok": True,
        "source": source,
        "family": family,
        "family_label": defaults["label"],
        "title": title or None,
        "width": defaults["width"],
        "height": defaults["height"],
        "steps": defaults["steps"],
        "cfg_scale": defaults["cfg_scale"],
        "prompt_style": defaults["prompt_style"],
        "message": f"Typical {defaults['label']} settings, {origin}.",
    }
