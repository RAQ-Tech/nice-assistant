"""Select a tested preset for a semantic request.

Planning used to assemble a combination from scored resource tags at request
time, which meant it could emit a checkpoint, workflow, and LoRA mix nobody had
run. It now chooses between presets an operator has already put together, and
the only thing still assembled is whatever fills a preset's declared open LoRA
slots. See ADR 0030.

Selection stays deterministic and explainable: hard requirements first, then
domain coverage, then operator priority, then estimated cost.
"""

from __future__ import annotations

import json

from app.identity_conditioning import IDENTITY_CONTROL_FEATURE
from app.model_prefill import checkpoint_role


PROVIDER_DEFAULT = "provider-default"
MAX_CONSIDERED_PRESETS = 12
RUNTIME_OPERATIONS = {
    ("openai-image", "openai"): {"generate"},
    ("local-image", "automatic1111"): {"generate"},
    ("local-image", "comfyui"): {"generate", "inpaint", "outpaint", "image_to_image"},
    ("openai-video", "openai"): {"generate"},
    ("local-video", "comfyui"): {"generate"},
}
SAMPLER_KEYS = ("steps", "cfg_scale", "sampler_name", "scheduler")
WORKFLOW_SETTING_KEYS = (
    "workflow_patch",
    "identity_image_bindings",
    "source_image_bindings",
    "mask_image_bindings",
    "prompt_bindings",
    "negative_prompt_bindings",
    "seed_bindings",
    "width_bindings",
    "height_bindings",
    "checkpoint_bindings",
    "required_prompt_token",
    "prompt_prefix",
)


def _json(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed


def _choose_preset(candidates, *, preferred_preset_id, persona_preset_ids, measured_preset_ids, default_model=""):
    """Pick the winner from candidates that already passed every requirement.

    Five tiers, most-informed first. The task model saw this request, so its
    choice outranks a standing preference. A persona preference then outranks
    everything below it, because "this recipe works for this face" is knowledge
    no score represents. Measured signals come next: what actually happened to
    the pictures each preset made. Then the one model a person has chosen by
    hand, on the Image Generation page - a request that named nothing should
    land there rather than on whichever recipe sorts first by name. The
    deterministic score is what remains when nothing else has anything to say,
    and it is also what happens when the model fails, times out, or expresses
    nothing.

    None of these tiers can select an incompatible preset. They only reorder a
    list the hard filter already produced.
    """

    def first_of(preset_ids):
        for preset_id in preset_ids:
            match = next((item for item in candidates if item["preset"].id == preset_id), None)
            if match:
                return match
        return None

    default_choice = next(
        (item for item in candidates if default_model and item["base"].external_id == default_model),
        None,
    )
    for source, choice in (
        ("task_model", first_of([preferred_preset_id] if preferred_preset_id else [])),
        ("persona_preference", first_of(persona_preset_ids)),
        ("measured_preference", first_of(measured_preset_ids)),
        ("default_model", default_choice),
    ):
        if choice:
            return choice, source
    return candidates[0], "deterministic"


def build_media_plan(repo, user_id: str, requirements: dict, providers, ready_backends=None) -> dict:
    """Choose an enabled preset that satisfies every hard requirement."""

    kind = requirements["kind"]
    operation = requirements["operation"]
    desired_domains = set(requirements["domains"])
    required_content = set(requirements["content_tags"])
    required_features = set(requirements["required_features"])
    preferred_preset_id = str(requirements.get("preferred_preset_id") or "")
    required_mechanism = str(requirements.get("required_identity_mechanism") or "")
    persona_preset_ids = [str(item) for item in (requirements.get("persona_preset_ids") or [])]
    # In-chat picture steering. "Another take" pins the recipe that made the
    # original, because silently switching recipes would make the button a
    # lie; "different look" sets that recipe aside so routing must choose
    # another. Both remain subject to every hard requirement.
    pinned_preset_id = str(requirements.get("pin_preset_id") or "")
    excluded_preset_ids = {str(item) for item in (requirements.get("exclude_preset_ids") or [])}

    presets = repo.media_presets(user_id, kind=kind, enabled=True)
    resources = {row.id: row for row in repo.media_catalog_resources(user_id, enabled=True)}
    compatibility = repo.media_compatibility_map(user_id)
    setting = repo.media_catalog_setting(user_id)
    preferences = (repo.settings(user_id) or {}).get("preferences") or {}
    default_model = str(preferences.get("image_local_model") or "").strip()

    rejected = []
    candidates = []
    for preset in presets:
        if pinned_preset_id and preset.id != pinned_preset_id:
            continue
        if preset.id in excluded_preset_ids:
            rejected.append(
                {"resource_id": preset.id, "name": preset.name, "reasons": ["set aside to get a different look"]}
            )
            continue
        definition = _json(preset.definition_json, {})
        evaluated = _evaluate_preset(
            preset,
            definition,
            resources=resources,
            compatibility=compatibility,
            setting=setting,
            providers=providers,
            ready_backends=ready_backends,
            operation=operation,
            desired_domains=desired_domains,
            required_content=required_content,
            required_features=required_features,
            required_mechanism=required_mechanism,
        )
        if evaluated["reasons"]:
            rejected.append({"resource_id": preset.id, "name": preset.name, "reasons": evaluated["reasons"]})
            continue
        candidates.append(evaluated)

    if not candidates:
        if pinned_preset_id:
            block_code = "pinned_preset_unavailable"
            block_message = "The recipe that made this picture can no longer serve it. Try a different look instead."
        elif excluded_preset_ids:
            block_code = "no_alternate_media_plan"
            block_message = (
                "There is no other recipe to try. Add another model in Media Catalog to get a different look."
            )
        else:
            block_code = "no_compatible_media_plan"
            block_message = _blocked_plan_message(rejected)
        return {
            "status": "blocked",
            "selected_resources": [],
            "execution_options": {},
            "explanation": {
                "summary": "No enabled generation preset can execute every hard requirement.",
                "selected": [],
                "warnings": [],
                "rejected": rejected[:20],
            },
            "estimated_vram_mb": 0,
            "block_code": block_code,
            "block_message": block_message,
        }

    candidates.sort(
        key=lambda item: (
            -item["domain_hits"],
            -item["preset"].priority,
            item["estimated_vram_mb"],
            item["preset"].name.casefold(),
            item["preset"].id,
        )
    )
    # The model's choice wins only if it survived the same hard filter every
    # other candidate did. Otherwise the deterministic order stands, which is
    # also what happens when the model fails, times out, or expresses nothing.
    winner, selection_source = _choose_preset(
        candidates,
        preferred_preset_id=preferred_preset_id,
        persona_preset_ids=persona_preset_ids,
        measured_preset_ids=requirements.get("measured_preset_ids") or [],
        default_model=default_model,
    )
    preferred = winner if selection_source == "task_model" else None
    preset = winner["preset"]
    snapshots = [_snapshot(item) for item in winner["selected"]]
    warnings = []
    if winner["missing_domains"]:
        warnings.append("No preset covered every preferred domain; missing: " + ", ".join(winner["missing_domains"]))
    if any(item.estimated_vram_mb == 0 for item in winner["selected"]):
        warnings.append("One or more selected resources have unknown VRAM requirements.")
    if preferred_preset_id and not preferred:
        warnings.append(
            "The requested preset did not meet this request's hard requirements, so the highest-scoring "
            "compatible preset was used instead."
        )
    if not (preset.routing_card or "").strip():
        warnings.append(
            f"Preset '{preset.name}' has no routing card, so it can only be chosen by tag coverage and priority."
        )
    for snapshot in snapshots:
        settings = snapshot["default_settings"]
        if (
            snapshot["resource_type"] == "workflow"
            and settings.get("workflow_patch")
            and not settings.get("prompt_bindings")
        ):
            warnings.append(
                f"Workflow '{snapshot['name']}' has no declared prompt binding, so the request may not reach the "
                "graph. Open it in Media Catalog and choose its prompt input."
            )
    return {
        "status": "ready",
        "selected_resources": snapshots,
        "execution_options": _execution_options(preset, winner, snapshots),
        "explanation": {
            "summary": (
                f"Selected the '{preset.name}' preset deterministically from enabled presets using hard "
                "requirements, domain coverage, operator priority, and estimated cost."
            ),
            "preset": {
                "id": preset.id,
                "name": preset.name,
                "revision": preset.revision,
                "priority": preset.priority,
                "routing_card": preset.routing_card or "",
                "source": selection_source,
                "reason": (
                    "chosen by the task model from the offered shortlist"
                    if selection_source == "task_model"
                    else "this persona's preferred recipe"
                    if selection_source == "persona_preference"
                    else "the model chosen on the Image Generation page, because nothing else preferred a recipe"
                    if selection_source == "default_model"
                    else _preset_reason(preset, winner, requirements)
                ),
                "considered": [
                    {"id": item["preset"].id, "name": item["preset"].name}
                    for item in candidates[:MAX_CONSIDERED_PRESETS]
                ],
            },
            "selected": [
                {
                    "resource_id": snapshot["id"],
                    "role": snapshot["resource_type"],
                    "name": snapshot["name"],
                    "reason": _selection_reason(snapshot, requirements),
                }
                for snapshot in snapshots
            ],
            "warnings": warnings,
            "rejected": rejected[:20],
        },
        "estimated_vram_mb": winner["estimated_vram_mb"],
        "block_code": None,
        "block_message": None,
    }


def _resolve_preset_resources(
    definition: dict,
    *,
    base,
    resources,
    compatibility,
    providers,
    ready_backends,
    operation: str,
    required_features: set[str],
) -> tuple:
    """Work out what this preset would actually run, and what is missing."""

    reasons = []
    if base.provider_key not in providers.media_providers:
        reasons.append("provider adapter is unavailable")
    if ready_backends is not None and (base.provider_key, base.backend) not in ready_backends:
        reasons.append("provider is not currently reachable")

    workflow = None
    workflow_id = definition.get("workflow_resource_id") or ""
    if workflow_id:
        workflow = resources.get(workflow_id)
        if not workflow or workflow.resource_type != "workflow":
            reasons.append("the preset's workflow is missing or disabled")
    elif (definition.get("workflow_slot") or {}).get("enabled"):
        # An open workflow slot is how a preset reaches a feature-capable graph
        # it does not name itself, which is what identity conditioning needs.
        workflow = _select_workflow(
            base_id=base.id,
            resources=resources,
            compatibility=compatibility,
            operation=operation,
            missing_features=required_features - set(_json(base.features_json, [])),
        )

    fixed_loras = []
    for item in definition.get("fixed_loras") or []:
        row = resources.get(item.get("resource_id") or "")
        if not row or row.resource_type != "lora":
            reasons.append("a LoRA this preset depends on is missing or disabled")
            continue
        fixed_loras.append((row, float(item.get("weight", 1.0))))
    return workflow, fixed_loras, reasons


def _requirement_failures(
    definition: dict,
    *,
    base,
    workflow,
    operation: str,
    required_content: set[str],
    required_features: set[str],
    required_mechanism: str,
    coverage_ops: set[str],
    coverage_content: set[str],
    coverage_features: set[str],
    stage_workflows: list | None = None,
) -> list[str]:
    """Every reason this preset cannot serve the request, named individually."""

    reasons = []
    if operation not in coverage_ops:
        reasons.append(f"operation '{operation}' is not declared compatible")
    if operation == "generate" and checkpoint_role(base.external_id) == "inpainting":
        # A model cataloged before the checkpoint list learned to tell an
        # inpainting model apart still says it generates. Its name says
        # otherwise, and a name-order tie once handed a request to one.
        reasons.append(
            "its filename says it is an inpainting checkpoint, which is not offered for making a picture from a prompt"
        )
    if operation != "generate" and not workflow:
        reasons.append(f"operation '{operation}' requires an explicit compatible ComfyUI workflow")
    missing_content = sorted(required_content - coverage_content)
    if missing_content:
        reasons.append("missing content tags: " + ", ".join(missing_content))
    missing_features = sorted(required_features - coverage_features)
    if missing_features:
        reasons.append("missing required features: " + ", ".join(missing_features))
    mechanisms = set(definition.get("identity_mechanisms") or [])
    for row in [workflow, *(stage_workflows or [])]:
        mechanisms |= workflow_mechanisms(row)
    if required_mechanism and required_mechanism not in mechanisms:
        # Named, because "this preset cannot do reference_adapter" tells the
        # operator what to fix; a generic rejection does not.
        reasons.append(f"does not implement the '{required_mechanism}' identity mechanism this persona requires")
    if operation not in RUNTIME_OPERATIONS.get((base.provider_key, base.backend), set()):
        reasons.append(f"the {base.backend} adapter does not yet execute '{operation}' workflows")
    return reasons


def _evaluate_preset(
    preset,
    definition: dict,
    *,
    resources,
    compatibility,
    setting,
    providers,
    ready_backends,
    operation: str,
    desired_domains: set[str],
    required_content: set[str],
    required_features: set[str],
    required_mechanism: str = "",
) -> dict:
    base = resources.get(definition.get("base_model_resource_id") or "")
    if not base or base.resource_type != "model":
        return {"preset": preset, "reasons": ["the preset's base model is missing or disabled"]}
    workflow, fixed_loras, reasons = _resolve_preset_resources(
        definition,
        base=base,
        resources=resources,
        compatibility=compatibility,
        providers=providers,
        ready_backends=ready_backends,
        operation=operation,
        required_features=required_features,
    )

    # Resolved before coverage, not after: a preset may do the identity work in
    # a later pass, and a capability only a later pass provides is still a
    # capability this preset has.
    stages, stage_reasons = _resolve_stages(definition, workflow, resources)
    reasons.extend(stage_reasons)
    stage_workflows = [row for _name, row in stages if row]

    coverage_domains = set(_json(preset.domains_json, [])) | set(_json(base.domains_json, []))
    coverage_content = set(_json(preset.content_tags_json, [])) | set(_json(base.content_tags_json, []))
    coverage_features = set(_json(preset.features_json, [])) | set(_json(base.features_json, []))
    coverage_ops = set(_json(preset.operations_json, [])) | set(_json(base.operations_json, []))
    for row in [workflow, *stage_workflows, *[lora for lora, _weight in fixed_loras]]:
        if not row:
            continue
        coverage_domains.update(_json(row.domains_json, []))
        coverage_content.update(_json(row.content_tags_json, []))
        coverage_features.update(_json(row.features_json, []))
        coverage_ops.update(_json(row.operations_json, []))

    slot_loras = _fill_slots(
        definition.get("lora_slots") or [],
        base_id=base.id,
        resources=resources,
        compatibility=compatibility,
        taken={row.id for row, _weight in fixed_loras},
        desired_domains=desired_domains,
        required_content=required_content,
        required_features=required_features,
        coverage_domains=coverage_domains,
        coverage_content=coverage_content,
        coverage_features=coverage_features,
        limit=max(0, int(setting.max_loras or 0)),
    )
    for row, _weight in slot_loras:
        coverage_domains.update(_json(row.domains_json, []))
        coverage_content.update(_json(row.content_tags_json, []))
        coverage_features.update(_json(row.features_json, []))
        coverage_ops.update(_json(row.operations_json, []))

    reasons.extend(
        _requirement_failures(
            definition,
            base=base,
            workflow=workflow,
            operation=operation,
            required_content=required_content,
            required_features=required_features,
            required_mechanism=required_mechanism,
            coverage_ops=coverage_ops,
            coverage_content=coverage_content,
            coverage_features=coverage_features,
            stage_workflows=stage_workflows,
        )
    )

    loras = fixed_loras + slot_loras
    selected = [base] + ([workflow] if workflow else []) + [row for row, _weight in loras]
    for row in stage_workflows:
        if row not in selected:
            selected.append(row)
    # ADR 0013: sequential stages never coexist, so the estimate is the base
    # plus the most expensive single stage, not the sum of all of them.
    resident = base.estimated_vram_mb + sum(row.estimated_vram_mb for row, _weight in loras)
    total_vram = resident + max([row.estimated_vram_mb for row in stage_workflows] or [0])
    if setting.vram_budget_mb and total_vram > setting.vram_budget_mb:
        reasons.append(f"estimated VRAM {total_vram} MB exceeds the {setting.vram_budget_mb} MB catalog budget")

    return {
        "preset": preset,
        "definition": definition,
        "base": base,
        "workflow": workflow,
        "loras": loras,
        "stages": stages,
        "selected": selected,
        "reasons": reasons,
        "domain_hits": len(desired_domains & coverage_domains),
        "estimated_vram_mb": total_vram,
        "missing_domains": sorted(desired_domains - coverage_domains),
    }


def _resolve_stages(definition: dict, workflow, resources) -> tuple[list, list]:
    """Resolve declared stages to their workflows, in order.

    Every stage after the first receives the previous stage's image, so it needs
    a workflow with real source bindings. A stage that cannot accept the picture
    it is handed is rejected here rather than discovered mid-generation.
    """

    declared = definition.get("stages") or []
    reasons = []
    stages = []
    for index, stage in enumerate(declared):
        name = str(stage.get("name") or f"stage{index + 1}")
        resource_id = stage.get("workflow_resource_id") or ""
        row = resources.get(resource_id) if resource_id else (workflow if index == 0 else None)
        if resource_id and (not row or row.resource_type != "workflow"):
            reasons.append(f"stage '{name}' names a workflow that is missing or disabled")
            continue
        if index and not row:
            reasons.append(f"stage '{name}' has no workflow, so it cannot receive the previous stage's image")
            continue
        if index and not _json(row.default_settings_json, {}).get("source_image_bindings"):
            reasons.append(f"stage '{name}' has no source image binding, so it cannot receive the previous image")
            continue
        stages.append((name, row))
    return stages, reasons


def workflow_mechanisms(workflow) -> set[str]:
    """Which conditioning mechanisms the selected graph actually provides.

    A preset's declared list is what the operator meant; this is what the plan
    can demonstrably do. Either is enough, which keeps a preset from being
    refused for a capability its attached workflow plainly has, and keeps a
    stored guess from being the only thing that decides.

    A graph that declares identity control, names where the reference goes, and
    can generate conditions during generation - `reference_adapter`. One that
    can only be handed a finished picture applies the face afterwards -
    `identity_pass`.
    """

    if not workflow:
        return set()
    settings = _json(workflow.default_settings_json, {})
    features = set(_json(workflow.features_json, []))
    if IDENTITY_CONTROL_FEATURE not in features or not settings.get("identity_image_bindings"):
        return set()
    if "generate" in set(_json(workflow.operations_json, [])):
        return {"reference_adapter"}
    if settings.get("source_image_bindings"):
        # It cannot make a picture, only change one it is handed. That is a
        # pass over a finished image rather than conditioning during one.
        return {"identity_pass"}
    return set()


def _select_workflow(*, base_id, resources, compatibility, operation, missing_features):
    """Pick the compatible workflow that best serves this request.

    The workflow must declare the operation. Covering a wanted feature is not a
    reason to run a graph that cannot do the job: an image-to-image identity
    workflow attached to a generate request has no source picture to work from,
    so it used to be selected here and then fail at upload time. Among graphs
    that can do the job, deterministic: feature overlap, then operator priority,
    then name.
    """

    candidates = []
    for row in resources.values():
        if row.resource_type != "workflow" or base_id not in compatibility.get(row.id, set()):
            continue
        if operation not in set(_json(row.operations_json, [])):
            continue
        features = set(_json(row.features_json, []))
        candidates.append((row, len(missing_features & features)))
    candidates.sort(key=lambda value: (-value[1], -value[0].priority, value[0].name.casefold(), value[0].id))
    return candidates[0][0] if candidates else None


def _fill_slots(
    slots,
    *,
    base_id,
    resources,
    compatibility,
    taken,
    desired_domains,
    required_content,
    required_features,
    coverage_domains,
    coverage_content,
    coverage_features,
    limit,
) -> list[tuple]:
    """Fill declared open slots only.

    This is the single place automatic LoRA selection still happens. A slot is
    the operator naming the one axis they are willing to let vary; the explicit
    catalog compatibility edges still decide what may fill it.
    """

    chosen = []
    used = set(taken)
    domains = set(coverage_domains)
    content = set(coverage_content)
    features = set(coverage_features)
    for slot in slots:
        allowed_domains = set(slot.get("domains") or [])
        allowed_content = set(slot.get("content_tags") or [])
        for _ in range(int(slot.get("max", 1))):
            if limit and len(chosen) >= limit:
                return chosen
            scored = []
            for row in resources.values():
                if row.resource_type != "lora" or row.id in used:
                    continue
                if base_id not in compatibility.get(row.id, set()):
                    continue
                row_domains = set(_json(row.domains_json, []))
                row_content = set(_json(row.content_tags_json, []))
                row_features = set(_json(row.features_json, []))
                if allowed_domains and not (row_domains & allowed_domains):
                    continue
                if allowed_content and not (row_content & allowed_content):
                    continue
                contribution = len((desired_domains - domains) & row_domains)
                contribution += 2 * len((required_content - content) & row_content)
                contribution += 2 * len((required_features - features) & row_features)
                if contribution:
                    scored.append((row, contribution))
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
            row = scored[0][0]
            used.add(row.id)
            weight = float(_json(row.default_settings_json, {}).get("weight", 1.0))
            chosen.append((row, weight))
            domains.update(_json(row.domains_json, []))
            content.update(_json(row.content_tags_json, []))
            features.update(_json(row.features_json, []))
    return chosen


def _blocked_plan_message(rejected: list[dict]) -> str:
    reasons = [reason for item in rejected for reason in item.get("reasons", [])]
    if any("missing required features: identity_control" in reason for reason in reasons):
        return (
            "This persona image requires identity conditioning, but no enabled compatible Media Catalog workflow "
            "provides identity_control. Open Settings → Media Catalog and add a tested ComfyUI workflow with an "
            "identity_control feature and explicit identity_image_bindings."
        )
    if not rejected:
        return (
            "No generation preset is enabled. Open Settings → Media Catalog and enable a preset, or enable a "
            "catalog model so one can be created for it."
        )
    return "No enabled generation preset satisfies this request."


def _preset_reason(preset, winner: dict, requirements: dict) -> str:
    matched = sorted(set(requirements.get("domains") or []) & set(_json(preset.domains_json, [])))
    if matched:
        return f"covers requested domains: {', '.join(matched)}"
    if winner["domain_hits"]:
        return "its resources cover the requested domains"
    return "highest-priority enabled preset that met every hard requirement"


def _selection_reason(snapshot: dict, requirements: dict) -> str:
    matched = []
    for field in ("domains", "content_tags", "features"):
        values = sorted(
            set(snapshot[field]) & set(requirements.get(field if field != "features" else "required_features", []))
        )
        if values:
            matched.append(f"{field.replace('_', ' ')}: {', '.join(values)}")
    return "; ".join(matched) or "named by the selected preset"


def _execution_options(preset, winner: dict, snapshots: list[dict]) -> dict:
    definition = winner["definition"]
    base = next(item for item in snapshots if item["resource_type"] == "model")
    workflow = next((item for item in snapshots if item["resource_type"] == "workflow"), None)
    settings = dict(base["default_settings"])
    settings.pop("prompt_dialect", None)
    for key, value in (definition.get("sampler") or {}).items():
        if key in SAMPLER_KEYS:
            settings[key] = value
    dimensions = definition.get("dimensions") or []
    if dimensions:
        settings["size"] = dimensions[0]
    if workflow:
        workflow_settings = workflow["default_settings"]
        for key in WORKFLOW_SETTING_KEYS:
            if workflow_settings.get(key):
                settings[key] = workflow_settings[key]
    external_id = base["external_id"]
    options = {
        "provider": "local" if base["provider_key"] in ("local-image", "local-video") else "openai",
        "backend": base["backend"],
        "model": external_id if external_id != PROVIDER_DEFAULT else None,
        "prompt_dialect": definition.get("prompt_dialect") or {},
        # Carried so execution can prove the preset has not changed since the
        # plan was written, exactly as resource revisions already are.
        "_preset_id": preset.id,
        "_preset_revision": preset.revision,
        **settings,
    }
    stages = winner.get("stages") or []
    if len(stages) > 1:
        options["stages"] = [
            {
                "name": name,
                # Which graph this pass runs, so a snapshot taken at planning
                # time can say which pass owns the identity bindings.
                "workflow_resource_id": row.id,
                **{
                    key: _json(row.default_settings_json, {}).get(key)
                    for key in WORKFLOW_SETTING_KEYS
                    if _json(row.default_settings_json, {}).get(key)
                },
            }
            for name, row in stages
        ]
    if winner["loras"]:
        options["loras"] = [
            {
                "name": row.external_id,
                "weight": weight,
                "trigger_words": _json(row.default_settings_json, {}).get("trigger_words", []),
            }
            for row, weight in winner["loras"]
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
