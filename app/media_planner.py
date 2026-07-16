from __future__ import annotations

import json


PROVIDER_DEFAULT = "provider-default"
RUNTIME_OPERATIONS = {
    ("openai-image", "openai"): {"generate"},
    ("local-image", "automatic1111"): {"generate"},
    ("local-image", "comfyui"): {"generate", "inpaint", "outpaint", "image_to_image"},
    ("openai-video", "openai"): {"generate"},
}


def _json(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def build_media_plan(repo, user_id: str, requirements: dict, providers, ready_backends=None) -> dict:
    """Select an explainable plan from explicit metadata; never inspect resource names."""
    resources = repo.media_catalog_resources(user_id, enabled=True)
    setting = repo.media_catalog_setting(user_id)
    compatibility = repo.media_compatibility_map(user_id)
    kind = requirements["kind"]
    operation = requirements["operation"]
    desired_domains = set(requirements["domains"])
    required_content = set(requirements["content_tags"])
    required_features = set(requirements["required_features"])
    models = [row for row in resources if row.resource_type == "model" and row.kind == kind]
    addons = [row for row in resources if row.resource_type != "model" and row.kind == kind]
    rejected = []
    candidates = []
    for model in models:
        reasons = []
        if model.provider_key not in providers.media_providers:
            reasons.append("provider adapter is unavailable")
        if ready_backends is not None and (model.provider_key, model.backend) not in ready_backends:
            reasons.append("provider is not currently reachable")
        model_ops = set(_json(model.operations_json, []))
        model_domains = set(_json(model.domains_json, []))
        model_content = set(_json(model.content_tags_json, []))
        model_features = set(_json(model.features_json, []))
        compatible = [
            item
            for item in addons
            if model.id in compatibility.get(item.id, set())
            and item.provider_key == model.provider_key
            and item.backend == model.backend
        ]
        missing_features = required_features - model_features
        workflow = _select_workflow(compatible, operation, missing_features)
        if operation != "generate" and not workflow:
            reasons.append(f"operation '{operation}' requires an explicit compatible ComfyUI workflow")
        selected = [model] + ([workflow] if workflow else [])
        coverage_domains = set(model_domains)
        coverage_content = set(model_content)
        coverage_features = set(model_features)
        coverage_ops = set(model_ops)
        if workflow:
            coverage_domains.update(_json(workflow.domains_json, []))
            coverage_content.update(_json(workflow.content_tags_json, []))
            coverage_features.update(_json(workflow.features_json, []))
            coverage_ops.update(_json(workflow.operations_json, []))
        loras = _select_loras(
            [item for item in compatible if item.resource_type == "lora"],
            desired_domains,
            required_content,
            required_features,
            coverage_domains,
            coverage_content,
            coverage_features,
            setting.max_loras,
        )
        selected.extend(loras)
        for lora in loras:
            coverage_domains.update(_json(lora.domains_json, []))
            coverage_content.update(_json(lora.content_tags_json, []))
            coverage_features.update(_json(lora.features_json, []))
            coverage_ops.update(_json(lora.operations_json, []))
        if operation not in coverage_ops:
            reasons.append(f"operation '{operation}' is not declared compatible")
        missing_content = sorted(required_content - coverage_content)
        if missing_content:
            reasons.append("missing content tags: " + ", ".join(missing_content))
        missing_features = sorted(required_features - coverage_features)
        if missing_features:
            reasons.append("missing required features: " + ", ".join(missing_features))
        runtime_ops = RUNTIME_OPERATIONS.get((model.provider_key, model.backend), set())
        if operation not in runtime_ops:
            reasons.append(f"the {model.backend} adapter does not yet execute '{operation}' workflows")
        total_vram = sum(item.estimated_vram_mb for item in selected)
        if setting.vram_budget_mb and total_vram > setting.vram_budget_mb:
            reasons.append(f"estimated VRAM {total_vram} MB exceeds the {setting.vram_budget_mb} MB catalog budget")
        if reasons:
            rejected.append({"resource_id": model.id, "name": model.name, "reasons": reasons})
            continue
        domain_hits = len(desired_domains & coverage_domains)
        priority = sum(item.priority for item in selected)
        candidates.append(
            {
                "model": model,
                "workflow": workflow,
                "loras": loras,
                "selected": selected,
                "domain_hits": domain_hits,
                "priority": priority,
                "estimated_vram_mb": total_vram,
                "missing_domains": sorted(desired_domains - coverage_domains),
            }
        )
    if not candidates:
        block_message = _blocked_plan_message(rejected)
        return {
            "status": "blocked",
            "selected_resources": [],
            "execution_options": {},
            "explanation": {
                "summary": "No enabled catalog model can execute every hard requirement.",
                "selected": [],
                "warnings": [],
                "rejected": rejected[:20],
            },
            "estimated_vram_mb": 0,
            "block_code": "no_compatible_media_plan",
            "block_message": block_message,
        }
    candidates.sort(
        key=lambda item: (
            -item["domain_hits"],
            -item["priority"],
            item["estimated_vram_mb"],
            item["model"].name.casefold(),
            item["model"].id,
        )
    )
    winner = candidates[0]
    snapshots = [_snapshot(item) for item in winner["selected"]]
    warnings = []
    if winner["missing_domains"]:
        warnings.append("No candidate covered every preferred domain; missing: " + ", ".join(winner["missing_domains"]))
    if any(item.estimated_vram_mb == 0 for item in winner["selected"]):
        warnings.append("One or more selected resources have unknown VRAM requirements.")
    explanation_selected = [
        {
            "resource_id": snapshot["id"],
            "role": snapshot["resource_type"],
            "name": snapshot["name"],
            "reason": _selection_reason(snapshot, requirements),
        }
        for snapshot in snapshots
    ]
    return {
        "status": "ready",
        "selected_resources": snapshots,
        "execution_options": _execution_options(snapshots),
        "explanation": {
            "summary": "Selected deterministically from enabled resources using compatibility, hard requirements, domain coverage, priority, and VRAM budget.",
            "selected": explanation_selected,
            "warnings": warnings,
            "rejected": rejected[:20],
        },
        "estimated_vram_mb": winner["estimated_vram_mb"],
        "block_code": None,
        "block_message": None,
    }


def _select_workflow(compatible, operation: str, missing_features: set[str]):
    candidates = []
    for item in compatible:
        if item.resource_type != "workflow":
            continue
        operations = set(_json(item.operations_json, []))
        features = set(_json(item.features_json, []))
        if operation not in operations and not (missing_features & features):
            continue
        coverage = len(missing_features & features) + (2 if operation in operations else 0)
        candidates.append((item, coverage))
    candidates.sort(key=lambda value: (-value[1], -value[0].priority, value[0].name.casefold(), value[0].id))
    return candidates[0][0] if candidates else None


def _blocked_plan_message(rejected: list[dict]) -> str:
    reasons = [reason for item in rejected for reason in item.get("reasons", [])]
    if any("missing required features: identity_control" in reason for reason in reasons):
        return (
            "This persona image requires identity conditioning, but no enabled compatible Media Catalog workflow "
            "provides identity_control. Open Settings → Media Catalog and add a tested ComfyUI workflow with an "
            "identity_control feature and explicit identity_image_bindings."
        )
    return "No enabled media catalog resources satisfy this request."


def _select_loras(
    candidates,
    desired_domains,
    required_content,
    required_features,
    coverage_domains,
    coverage_content,
    coverage_features,
    limit,
):
    selected = []
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        scored = []
        for item in remaining:
            domains = set(_json(item.domains_json, []))
            content = set(_json(item.content_tags_json, []))
            features = set(_json(item.features_json, []))
            contribution = len((desired_domains - coverage_domains) & domains)
            contribution += 2 * len((required_content - coverage_content) & content)
            contribution += 2 * len((required_features - coverage_features) & features)
            if contribution:
                scored.append((item, contribution))
        if not scored:
            break
        scored.sort(
            key=lambda value: (
                -value[1],
                -value[0].priority,
                value[0].estimated_vram_mb,
                value[0].name.casefold(),
                value[0].id,
            )
        )
        chosen = scored[0][0]
        selected.append(chosen)
        coverage_domains.update(_json(chosen.domains_json, []))
        coverage_content.update(_json(chosen.content_tags_json, []))
        coverage_features.update(_json(chosen.features_json, []))
        remaining = [item for item in remaining if item.id != chosen.id]
    return selected


def _selection_reason(snapshot: dict, requirements: dict) -> str:
    matched = []
    for field in ("domains", "content_tags", "features"):
        values = sorted(
            set(snapshot[field]) & set(requirements.get(field if field != "features" else "required_features", []))
        )
        if values:
            matched.append(f"{field.replace('_', ' ')}: {', '.join(values)}")
    return "; ".join(matched) or "compatible enabled default selected by priority"


def _execution_options(snapshots: list[dict]) -> dict:
    model = next(item for item in snapshots if item["resource_type"] == "model")
    workflow = next((item for item in snapshots if item["resource_type"] == "workflow"), None)
    loras = [item for item in snapshots if item["resource_type"] == "lora"]
    settings = dict(model["default_settings"])
    if workflow:
        workflow_settings = workflow["default_settings"]
        for key, value in workflow_settings.items():
            if key != "workflow_patch":
                settings[key] = value
        if workflow_settings.get("workflow_patch"):
            settings["workflow_patch"] = workflow_settings["workflow_patch"]
    external_id = model["external_id"]
    provider = "local" if model["provider_key"] == "local-image" else "openai"
    options = {
        "provider": provider,
        "backend": model["backend"],
        "model": external_id if external_id != PROVIDER_DEFAULT else None,
        **settings,
    }
    if loras:
        options["loras"] = [
            {
                "name": item["external_id"],
                "weight": float(item["default_settings"].get("weight", 1.0)),
                "trigger_words": item["default_settings"].get("trigger_words", []),
            }
            for item in loras
        ]
    return options


def _snapshot(row) -> dict:
    return {
        "id": row.id,
        "resource_type": row.resource_type,
        "name": row.name,
        "provider_key": row.provider_key,
        "backend": row.backend,
        "external_id": row.external_id,
        "domains": _json(row.domains_json, []),
        "content_tags": _json(row.content_tags_json, []),
        "features": _json(row.features_json, []),
        "estimated_vram_mb": row.estimated_vram_mb,
        "default_settings": _json(row.default_settings_json, {}),
        "updated_at": row.updated_at,
        "revision": row.revision,
    }
