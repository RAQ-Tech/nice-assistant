"""Turning a preset here into a file that means something somewhere else.

A preset in the catalog is written in this installation's resource IDs, which
are meaningless anywhere else. Export rewrites it in the names a person would
recognise - the filenames the provider reports - and refuses to carry anything
measured on this machine.

Two things leave and one thing does not. What travels is the recipe: the model
and LoRAs by name, the sampler settings, the dimensions, the prompt dialect.
What does not travel is a workflow graph, because it is a local artifact with
this installation's node numbering inside it, and possibly local paths. Rather
than dropping it and producing a preset that quietly behaves differently, the
export names it as a requirement: this recipe expects a workflow called X, and
whoever imports it has to supply their own.

VRAM estimates are measured on the machine that measured them. They are removed
rather than exported as fact about somebody else's hardware.
"""

from __future__ import annotations

from app.preset_bundle import BUNDLE_VERSION


# Everything a bundle entry may carry. Anything else in a preset either does not
# survive the trip or is specific to this installation.
PORTABLE_SAMPLER_KEYS = ("steps", "cfg_scale", "sampler_name", "scheduler")


def _resource_name(resources: dict, resource_id: str) -> str:
    row = resources.get(resource_id or "")
    return str(getattr(row, "external_id", "") or "") if row else ""


def _requirement(label: str, name: str) -> str:
    return f"{label}: {name}" if name else label


def export_entry(preset, definition: dict, resources: dict) -> dict:
    """Rewrite one preset as a bundle entry, and say what could not travel.

    `resources` maps resource id to catalog row. Anything a preset references
    that cannot be named portably becomes a requirement rather than silently
    disappearing, because a recipe that arrives missing a piece it never
    mentioned is worse than one that arrives asking for it.
    """

    requirements = []
    base_name = _resource_name(resources, definition.get("base_model_resource_id"))
    if not base_name:
        # Nothing to export against: the recipe would arrive pointing at
        # nothing at all.
        requirements.append(_requirement("A base model this installation could not name", ""))

    lora_names = []
    for item in definition.get("fixed_loras") or []:
        name = _resource_name(resources, item.get("resource_id"))
        if name:
            lora_names.append(name)
        else:
            requirements.append(_requirement("A LoRA this installation could not name", ""))

    workflow_name = _resource_name(resources, definition.get("workflow_resource_id"))
    if definition.get("workflow_resource_id"):
        requirements.append(
            _requirement("A ComfyUI workflow, which does not travel between installations", workflow_name)
        )
    for index, stage in enumerate(definition.get("stages") or [], start=1):
        stage_workflow = _resource_name(resources, stage.get("workflow_resource_id"))
        if stage.get("workflow_resource_id"):
            requirements.append(
                _requirement(f"A ComfyUI workflow for pass {index}, which does not travel", stage_workflow)
            )
    for mechanism in definition.get("identity_mechanisms") or []:
        requirements.append(_requirement("An identity mechanism this preset implements", str(mechanism)))

    entry = {
        "name": preset.name,
        "routing_card": preset.routing_card or "",
        "notes": preset.notes or "",
        "kind": preset.kind,
        "priority": int(preset.priority or 50),
        "operations": list(_json_list(preset.operations_json)),
        "domains": list(_json_list(preset.domains_json)),
        "content_tags": list(_json_list(preset.content_tags_json)),
        "features": list(_json_list(preset.features_json)),
        "base_model_external_id": base_name,
        "lora_external_ids": lora_names,
        "sampler": {
            key: value
            for key, value in (definition.get("sampler") or {}).items()
            if key in PORTABLE_SAMPLER_KEYS and value not in (None, "")
        },
        "dimensions": list(definition.get("dimensions") or []),
        "prompt_dialect": dict(definition.get("prompt_dialect") or {}),
        "lora_slots": list(definition.get("lora_slots") or []),
        "workflow_slot": dict(definition.get("workflow_slot") or {"enabled": False}),
        "requirements": requirements,
    }
    return entry


def export_bundle(entries: list[dict]) -> dict:
    return {"version": BUNDLE_VERSION, "presets": entries}


def preview(entry: dict) -> list[dict]:
    """What will leave, field by field, before the file is written.

    A list rather than a rendered blob, so the browser shows it as rows an
    operator reads rather than JSON they scroll past.
    """

    rows = [
        ("Name", entry["name"]),
        ("Kind", entry["kind"]),
        ("When to use it", entry["routing_card"] or "Not written"),
        ("Notes", entry["notes"] or "None"),
        ("Model file", entry["base_model_external_id"] or "Could not be named"),
        ("LoRA files", ", ".join(entry["lora_external_ids"]) or "None"),
        ("Operations", ", ".join(entry["operations"]) or "None"),
        ("Domains", ", ".join(entry["domains"]) or "None"),
        ("Content tags", ", ".join(entry["content_tags"]) or "None"),
        ("Features", ", ".join(entry["features"]) or "None"),
        ("Sampler", _pairs(entry["sampler"]) or "Provider defaults"),
        ("Dimensions", ", ".join(entry["dimensions"]) or "Provider defaults"),
        ("Prompt style", str((entry["prompt_dialect"] or {}).get("style") or "Default")),
    ]
    return [{"label": label, "value": value} for label, value in rows]


def withheld() -> list[str]:
    """What is deliberately not in the file, said out loud.

    An operator sharing a preset should be able to see that nothing about their
    machine is inside it, rather than take that on trust.
    """

    return [
        "VRAM estimates, which were measured on this machine",
        "Provider addresses and any local file path",
        "This installation's resource identifiers",
        "Workflow graphs, which are named as requirements instead",
    ]


def _pairs(values: dict) -> str:
    return ", ".join(f"{key} {value}" for key, value in sorted((values or {}).items()))


def _json_list(value) -> list:
    import json

    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
