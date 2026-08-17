"""Known-good ComfyUI graphs, shipped with their bindings already declared.

Setting identity conditioning up used to mean exporting a graph from ComfyUI in
API format, reading its nodes, and choosing which input receives the prompt and
which receives the reference. That is a node-graph task wearing a settings-page
costume, and it is the part of the work a person should not have to do.

A template is the other direction. The graph is written here, its node IDs are
fixed, and its bindings are declared by construction rather than discovered.
Inspection stops being discovery and becomes verification: are these node types
installed, and are the files these nodes name actually present?

Two things it deliberately does not do. It does not claim the graph has been run
on this deployment - nothing here has - and it does not pretend to check assets
it cannot see. An identity model loaded behind a device selector rather than a
named input is invisible to `/object_info`, so a template states what it needs in
plain language instead of implying it was verified.

This is a sibling of `app/preset_bundle.py`, not an extension of it. A bundle
deliberately cannot carry a graph: it is the operator-to-operator import path,
where accepting one would mean running a stranger's graph on your machine.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.identity_conditioning import CONDITIONING_MECHANISMS
from app.service_errors import NotFoundError, RequestError


TEMPLATE_VERSION = 1
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "assets" / "workflow-templates"

# What family a checkpoint belongs to. Declared by the operator, never sniffed:
# the application has no access to the models directory, and `/object_info`
# reports the filenames it has rather than what is inside them. It matters
# because an identity adapter is trained against one text encoder, and the
# families below that share a base architecture do not share that encoder.
MODEL_ARCHITECTURES = (
    "sd15",
    "sdxl",
    "pony",
    "illustrious",
    "sd3",
    "flux",
    "chroma",
    "other",
)

BINDING_ROLES = (
    "prompt_bindings",
    "negative_prompt_bindings",
    "seed_bindings",
    "width_bindings",
    "height_bindings",
    "checkpoint_bindings",
    "identity_image_bindings",
    "source_image_bindings",
    "mask_image_bindings",
)

_ALLOWED_FIELDS = {
    "version",
    "id",
    "name",
    "template_version",
    "summary",
    "mechanism",
    "architectures",
    "operations",
    "features",
    "domains",
    "required_assets",
    "required_prompt_token",
    "prompt_prefix",
    "workflow",
    "bindings",
}

_SLUG = "abcdefghijklmnopqrstuvwxyz0123456789-"


def _text(value, label: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise RequestError(f"workflow template {label} is too long", 500)
    return text


def _strings(values, label: str, *, limit: int, count: int) -> list[str]:
    if not isinstance(values, list) or len(values) > count:
        raise RequestError(f"workflow template {label} is invalid", 500)
    return [_text(item, label, limit=limit) for item in values]


def normalize_template(values) -> dict:
    """Validate a template's shape. Whether it can run here is checked later."""

    if not isinstance(values, dict):
        raise RequestError("a workflow template must be an object", 500)
    unknown = set(values) - _ALLOWED_FIELDS
    if unknown:
        raise RequestError(f"workflow template includes unsupported fields: {', '.join(sorted(unknown))}", 500)
    if int(values.get("version") or 0) != TEMPLATE_VERSION:
        raise RequestError("workflow template version is not supported", 500)
    identifier = _text(values.get("id"), "id", limit=64).casefold()
    if not identifier or set(identifier) - set(_SLUG):
        raise RequestError("workflow template id must be a lowercase slug", 500)
    mechanism = _text(values.get("mechanism"), "mechanism", limit=64)
    if mechanism not in CONDITIONING_MECHANISMS:
        raise RequestError(f"workflow template '{identifier}' declares an unknown mechanism", 500)
    architectures = _strings(values.get("architectures"), "architectures", limit=32, count=16)
    unsupported = sorted(set(architectures) - set(MODEL_ARCHITECTURES))
    if not architectures or unsupported:
        raise RequestError(f"workflow template '{identifier}' names unknown architectures", 500)
    workflow = values.get("workflow")
    if not isinstance(workflow, dict) or not workflow:
        raise RequestError(f"workflow template '{identifier}' has no graph", 500)
    token = _text(values.get("required_prompt_token"), "prompt token", limit=64)
    prefix = _text(values.get("prompt_prefix"), "prompt prefix", limit=300)
    _check_prompt_token(identifier, token, prefix)
    return {
        "id": identifier,
        "name": _text(values.get("name"), "name", limit=120) or identifier,
        "template_version": max(1, int(values.get("template_version") or 1)),
        "summary": _text(values.get("summary"), "summary", limit=2000),
        "mechanism": mechanism,
        "architectures": architectures,
        "operations": _strings(values.get("operations") or ["generate"], "operations", limit=32, count=8),
        "features": _strings(values.get("features") or [], "features", limit=64, count=16),
        "domains": _strings(values.get("domains") or [], "domains", limit=64, count=16),
        # Plain language, because these are the parts nothing can check. A file
        # that a node loads through a device selector rather than a named input
        # is not in `/object_info` at all.
        "required_assets": _strings(values.get("required_assets") or [], "required assets", limit=300, count=16),
        # A technique whose conditioning only happens when a particular word is
        # in the prompt fails silently without it, and still returns a picture.
        "required_prompt_token": token,
        "prompt_prefix": prefix,
        "workflow": workflow,
        "bindings": _normalize_bindings(identifier, values.get("bindings")),
    }


def _check_prompt_token(identifier: str, token: str, prefix: str) -> None:
    if token and token not in prefix:
        # Otherwise the prefix that exists to guarantee the token would not
        # contain it, and the technique would quietly do nothing.
        raise RequestError(
            f"workflow template '{identifier}' requires the prompt token '{token}' "
            "but its prompt prefix does not contain it",
            500,
        )


def _normalize_bindings(identifier: str, values) -> dict:
    if not isinstance(values, dict):
        raise RequestError(f"workflow template '{identifier}' has no bindings", 500)
    unknown = sorted(set(values) - set(BINDING_ROLES))
    if unknown:
        raise RequestError(f"workflow template '{identifier}' binds unknown roles: {', '.join(unknown)}", 500)
    bindings = {}
    for role in BINDING_ROLES:
        entries = values.get(role) or []
        if not isinstance(entries, list) or len(entries) > 8:
            raise RequestError(f"workflow template '{identifier}' has an invalid {role} list", 500)
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"node_id", "input_name"}:
                raise RequestError(f"workflow template '{identifier}' has a malformed {role} entry", 500)
        if entries:
            bindings[role] = [
                {"node_id": str(entry["node_id"]), "input_name": str(entry["input_name"])} for entry in entries
            ]
    if not bindings.get("prompt_bindings"):
        # The rule every workflow already lives under: a graph that cannot
        # receive the request renders the text saved inside it and still
        # returns a picture, so the failure would be invisible.
        raise RequestError(f"workflow template '{identifier}' must bind the request prompt", 500)
    return bindings


def available_templates() -> list[dict]:
    """Every shipped template, by id."""

    templates = []
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RequestError(f"workflow template '{path.stem}' could not be read", 500) from exc
        templates.append(normalize_template(raw))
    return templates


def resolve_template(template_id: str) -> dict:
    """One shipped template, or a not-found the caller can report."""

    wanted = str(template_id or "").strip().casefold()
    for template in available_templates():
        if template["id"] == wanted:
            return template
    raise NotFoundError("workflow template not found")


def template_default_settings(template: dict) -> dict:
    """The workflow resource default settings this template would install."""

    settings = {"workflow_patch": json.loads(json.dumps(template["workflow"])), **template["bindings"]}
    if template["required_prompt_token"]:
        settings["required_prompt_token"] = template["required_prompt_token"]
        settings["prompt_prefix"] = template["prompt_prefix"]
    return settings
