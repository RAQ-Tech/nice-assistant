"""The serialized preset bundle, and the starter recipes shipped in one.

A bundle names assets the way a person does - by the filename the provider
reports - rather than by this installation's resource IDs, because those mean
nothing anywhere else. Installing resolves those names against the owner's
catalog and against what the provider actually reports as installed.

This is the format the built-in starters ship through. It is also the format
export and import will use later, which is why it is written now rather than
after: a starter set and a shared preset are the same artifact.

Starter recipes carry published defaults for a model family - the sampler, step
count, CFG, dimensions, and prompt dialect that family expects. They are a
starting point, not a measurement: nothing here has been tested on this
deployment, and the product says so wherever they are offered.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.media_preset import normalize_definition
from app.service_errors import RequestError


BUNDLE_VERSION = 1
STARTER_BUNDLE_PATH = Path(__file__).resolve().parents[1] / "assets" / "preset-bundles" / "starters.json"
MAX_BUNDLE_PRESETS = 64


def _text(value, label: str, *, limit: int = 200) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise RequestError(f"preset bundle {label} is too long", 400)
    return text


def normalize_bundle(values) -> dict:
    """Validate a bundle's shape. Asset availability is checked at install."""

    if not isinstance(values, dict):
        raise RequestError("a preset bundle must be an object", 400)
    unknown = set(values) - {"version", "presets"}
    if unknown:
        raise RequestError(f"preset bundle includes unsupported fields: {', '.join(sorted(unknown))}", 400)
    try:
        version = int(values.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise RequestError("preset bundle version is invalid", 400) from exc
    if version != BUNDLE_VERSION:
        raise RequestError(f"preset bundle version {version} is not supported", 400)
    presets = values.get("presets")
    if not isinstance(presets, list) or not presets:
        raise RequestError("a preset bundle must contain at least one preset", 400)
    if len(presets) > MAX_BUNDLE_PRESETS:
        raise RequestError(f"a preset bundle may contain at most {MAX_BUNDLE_PRESETS} presets", 400)
    return {"version": version, "presets": [_normalize_entry(item) for item in presets]}


def _normalize_entry(values) -> dict:
    if not isinstance(values, dict):
        raise RequestError("each bundle preset must be an object", 400)
    allowed = {
        "name",
        "routing_card",
        "notes",
        "kind",
        "priority",
        "operations",
        "domains",
        "content_tags",
        "features",
        "base_model_external_id",
        "lora_external_ids",
        "sampler",
        "dimensions",
        "prompt_dialect",
        "lora_slots",
        "workflow_slot",
        # Things the recipe needs that a file cannot carry: a workflow graph, an
        # identity mechanism, an asset this installation could not name. Present
        # so an import can say what is missing rather than behaving differently
        # without saying why.
        "requirements",
        # Accepted so an older or foreign file is not refused outright, and
        # then dropped: a VRAM figure is a measurement of the machine that
        # measured it, and it is not a fact about this one.
        "estimated_vram_mb",
    }
    unknown = set(values) - allowed
    if unknown:
        raise RequestError(f"bundle preset includes unsupported fields: {', '.join(sorted(unknown))}", 400)
    name = _text(values.get("name"), "name", limit=120)
    if not name:
        raise RequestError("each bundle preset needs a name", 400)
    base = _text(values.get("base_model_external_id"), "base model", limit=300)
    if not base:
        raise RequestError(f"bundle preset '{name}' must name the model file it expects", 400)
    loras = values.get("lora_external_ids") or []
    if not isinstance(loras, list) or len(loras) > 8:
        raise RequestError(f"bundle preset '{name}' has an invalid LoRA list", 400)
    # Validate the definition shape now, with a placeholder identifier, so a
    # malformed bundle is refused before anything touches the catalog.
    normalize_definition(
        {
            "base_model_resource_id": "placeholder",
            "sampler": values.get("sampler"),
            "dimensions": values.get("dimensions"),
            "prompt_dialect": values.get("prompt_dialect"),
            "lora_slots": values.get("lora_slots"),
            "workflow_slot": values.get("workflow_slot"),
        }
    )
    return {
        "name": name,
        "routing_card": _text(values.get("routing_card"), "routing card", limit=2000),
        "notes": _text(values.get("notes"), "notes", limit=4000),
        "kind": str(values.get("kind") or "image"),
        "priority": int(values.get("priority", 50)),
        "operations": list(values.get("operations") or ["generate"]),
        "domains": list(values.get("domains") or []),
        "content_tags": list(values.get("content_tags") or []),
        "features": list(values.get("features") or []),
        "base_model_external_id": base,
        "lora_external_ids": [_text(item, "LoRA", limit=300) for item in loras],
        "sampler": values.get("sampler") or {},
        "dimensions": values.get("dimensions") or [],
        "prompt_dialect": values.get("prompt_dialect") or {},
        "lora_slots": values.get("lora_slots") or [],
        "workflow_slot": values.get("workflow_slot") or {"enabled": False},
        "requirements": [_text(item, "requirement", limit=300) for item in (values.get("requirements") or [])][:16],
    }


def starter_bundle() -> dict:
    """Load the shipped starter recipes."""

    try:
        raw = json.loads(STARTER_BUNDLE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequestError("the starter preset bundle could not be read", 500) from exc
    return normalize_bundle(raw)


def resolve_entry(entry: dict, resources) -> dict:
    """Match a bundle entry's named assets against the owner's catalog.

    Returns what could be resolved and what could not, by name. A recipe whose
    checkpoint is not installed is reported here rather than failing later
    during generation, where the operator has no useful way to act on it.
    """

    kind = entry["kind"]
    by_external_id = {(row.resource_type, row.kind, row.external_id): row for row in resources if row.enabled}
    missing = []
    base = by_external_id.get(("model", kind, entry["base_model_external_id"]))
    if not base:
        missing.append(entry["base_model_external_id"])
    loras = []
    for external_id in entry["lora_external_ids"]:
        row = by_external_id.get(("lora", kind, external_id))
        if row:
            loras.append(row)
        else:
            missing.append(external_id)
    return {"base": base, "loras": loras, "missing_assets": missing}
