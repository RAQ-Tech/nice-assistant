"""Generation preset bodies.

A preset is the tested recipe: which checkpoint, which workflow graph, which
LoRAs at which weights, the sampler settings, the permitted dimensions, and the
prompt dialect that combination was tuned with. Planning selects one of these
rather than assembling a combination nobody has run, which is the change
ADR 0030 records.

Shape validation lives here and is pure. Whether a referenced resource exists,
is enabled, and is explicitly compatible is a question about the owner's
catalog, so the service answers that.
"""

from __future__ import annotations

import re

from app.identity_conditioning import CONDITIONING_MECHANISMS
from app.prompt_dialect import normalize_dialect
from app.service_errors import RequestError


DIMENSION_PATTERN = re.compile(r"^([1-9][0-9]{1,4})x([1-9][0-9]{1,4})$")
SLOT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_DIMENSIONS = 12
MAX_FIXED_LORAS = 8
MAX_SLOTS = 4
MAX_STAGES = 4
SAMPLER_KEYS = ("steps", "cfg_scale", "sampler_name", "scheduler")
DEFINITION_KEYS = (
    "base_model_resource_id",
    "workflow_resource_id",
    "prompt_dialect",
    "sampler",
    "dimensions",
    "fixed_loras",
    "lora_slots",
    "workflow_slot",
    "identity_mechanisms",
    "stages",
)


def _identifier(value, label: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise RequestError(f"preset {label} is required", 400)
        return ""
    if len(text) > 64:
        raise RequestError(f"preset {label} is invalid", 400)
    return text


def _sampler(values) -> dict:
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise RequestError("preset sampler settings must be an object", 400)
    unknown = set(values) - set(SAMPLER_KEYS)
    if unknown:
        raise RequestError(f"preset sampler settings include unsupported fields: {', '.join(sorted(unknown))}", 400)
    result = {}
    if "steps" in values and values["steps"] is not None:
        try:
            result["steps"] = int(values["steps"])
        except (TypeError, ValueError) as exc:
            raise RequestError("preset steps must be a whole number", 400) from exc
        if not 1 <= result["steps"] <= 500:
            raise RequestError("preset steps must be between 1 and 500", 400)
    if "cfg_scale" in values and values["cfg_scale"] is not None:
        try:
            result["cfg_scale"] = float(values["cfg_scale"])
        except (TypeError, ValueError) as exc:
            raise RequestError("preset CFG scale must be a number", 400) from exc
        if not 0 <= result["cfg_scale"] <= 50:
            raise RequestError("preset CFG scale must be between 0 and 50", 400)
    for key in ("sampler_name", "scheduler"):
        if key in values and values[key] is not None:
            text = " ".join(str(values[key]).split()).strip()
            if len(text) > 200:
                raise RequestError(f"preset {key.replace('_', ' ')} is invalid", 400)
            if text:
                result[key] = text
    return result


def _dimensions(values) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise RequestError("preset dimensions must be a list", 400)
    if len(values) > MAX_DIMENSIONS:
        raise RequestError(f"a preset may declare at most {MAX_DIMENSIONS} dimensions", 400)
    result = []
    for value in values:
        text = str(value or "").strip().lower().replace(" ", "")
        if not DIMENSION_PATTERN.fullmatch(text):
            raise RequestError(f"preset dimension '{value}' must look like 1024x1024", 400)
        if text not in result:
            result.append(text)
    return result


def _fixed_loras(values) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise RequestError("preset LoRAs must be a list", 400)
    if len(values) > MAX_FIXED_LORAS:
        raise RequestError(f"a preset may declare at most {MAX_FIXED_LORAS} LoRAs", 400)
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {"resource_id", "weight"}:
            raise RequestError("each preset LoRA needs a resource_id and an optional weight", 400)
        resource_id = _identifier(value.get("resource_id"), "LoRA resource id")
        if resource_id in seen:
            raise RequestError("a preset cannot list the same LoRA twice", 400)
        seen.add(resource_id)
        try:
            weight = float(value.get("weight", 1.0))
        except (TypeError, ValueError) as exc:
            raise RequestError("preset LoRA weight must be a number", 400) from exc
        if not 0 <= weight <= 4:
            raise RequestError("preset LoRA weight must be between 0 and 4", 400)
        result.append({"resource_id": resource_id, "weight": weight})
    return result


def _lora_slots(values) -> list[dict]:
    """Open slots are the only place automatic LoRA selection still applies.

    Everything else about a preset is a tested choice; a slot is the operator
    saying which one axis may still vary, and by how much.
    """

    if values is None:
        return []
    if not isinstance(values, list):
        raise RequestError("preset LoRA slots must be a list", 400)
    if len(values) > MAX_SLOTS:
        raise RequestError(f"a preset may declare at most {MAX_SLOTS} LoRA slots", 400)
    result = []
    names = set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {"name", "max", "domains", "content_tags"}:
            raise RequestError("each preset LoRA slot needs a name, max, and optional filters", 400)
        name = str(value.get("name") or "").strip().lower()
        if not SLOT_NAME_PATTERN.fullmatch(name):
            raise RequestError("preset LoRA slot names must be short lowercase identifiers", 400)
        if name in names:
            raise RequestError("preset LoRA slot names must be unique", 400)
        names.add(name)
        try:
            maximum = int(value.get("max", 1))
        except (TypeError, ValueError) as exc:
            raise RequestError("preset LoRA slot max must be a whole number", 400) from exc
        if not 1 <= maximum <= MAX_FIXED_LORAS:
            raise RequestError(f"preset LoRA slot max must be between 1 and {MAX_FIXED_LORAS}", 400)
        result.append(
            {
                "name": name,
                "max": maximum,
                "domains": _tag_list(value.get("domains"), "slot domains"),
                "content_tags": _tag_list(value.get("content_tags"), "slot content tags"),
            }
        )
    return result


def _tag_list(values, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 32:
        raise RequestError(f"preset {label} must be a list of at most 32 values", 400)
    result = []
    for value in values:
        text = str(value or "").strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def _stages(values, workflow_resource_id: str) -> list[dict]:
    """Declared passes. A preset with no explicit stages is single-pass."""

    if not values:
        return [{"name": "base", "workflow_resource_id": workflow_resource_id}]
    if not isinstance(values, list):
        raise RequestError("preset stages must be a list", 400)
    if len(values) > MAX_STAGES:
        raise RequestError(f"a preset may declare at most {MAX_STAGES} stages", 400)
    result = []
    names = set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {"name", "workflow_resource_id"}:
            raise RequestError("each preset stage needs a name and an optional workflow", 400)
        name = str(value.get("name") or "").strip().lower()
        if not SLOT_NAME_PATTERN.fullmatch(name):
            raise RequestError("preset stage names must be short lowercase identifiers", 400)
        if name in names:
            raise RequestError("preset stage names must be unique", 400)
        names.add(name)
        result.append(
            {
                "name": name,
                "workflow_resource_id": _identifier(
                    value.get("workflow_resource_id"), "stage workflow", required=False
                ),
            }
        )
    return result


def _workflow_slot(values) -> dict:
    """May a compatible workflow be attached to satisfy a required feature?

    Off by default: a preset is a fixed recipe. Presets created from an existing
    catalog model turn it on, because attaching a feature-capable workflow at
    request time is exactly what the coordinator did before presets existed.
    """

    if values is None:
        return {"enabled": False}
    if not isinstance(values, dict) or set(values) - {"enabled"}:
        raise RequestError("preset workflow slot must be an object with an enabled flag", 400)
    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RequestError("preset workflow slot enabled must be true or false", 400)
    return {"enabled": enabled}


def _identity_mechanisms(values) -> list[str]:
    """Which ways of producing resemblance this recipe can actually apply.

    Declared, never inferred: a graph either has the wiring for a mechanism or
    it does not, and guessing would let a persona image run against a preset
    that cannot honor its Identity Spec.
    """

    if values is None:
        return []
    if not isinstance(values, list) or len(values) > len(CONDITIONING_MECHANISMS):
        raise RequestError("preset identity mechanisms must be a list", 400)
    result = []
    for value in values:
        text = str(value or "").strip()
        if text not in CONDITIONING_MECHANISMS:
            raise RequestError(f"preset identity mechanism must be one of {', '.join(CONDITIONING_MECHANISMS)}", 400)
        if text not in result:
            result.append(text)
    return result


def normalize_definition(values) -> dict:
    """Validate a preset body's shape. Reference checks belong to the service."""

    if not isinstance(values, dict):
        raise RequestError("preset definition must be an object", 400)
    unknown = set(values) - set(DEFINITION_KEYS)
    if unknown:
        raise RequestError(f"preset definition includes unsupported fields: {', '.join(sorted(unknown))}", 400)
    workflow_resource_id = _identifier(values.get("workflow_resource_id"), "workflow", required=False)
    return {
        "base_model_resource_id": _identifier(values.get("base_model_resource_id"), "base model"),
        "workflow_resource_id": workflow_resource_id,
        "prompt_dialect": normalize_dialect(values.get("prompt_dialect")),
        "sampler": _sampler(values.get("sampler")),
        "dimensions": _dimensions(values.get("dimensions")),
        "fixed_loras": _fixed_loras(values.get("fixed_loras")),
        "lora_slots": _lora_slots(values.get("lora_slots")),
        "workflow_slot": _workflow_slot(values.get("workflow_slot")),
        "identity_mechanisms": _identity_mechanisms(values.get("identity_mechanisms")),
        "stages": _stages(values.get("stages"), workflow_resource_id),
    }


def default_dimension(definition: dict) -> str:
    """The first declared dimension is the preset's default."""

    dimensions = definition.get("dimensions") or []
    return dimensions[0] if dimensions else ""
