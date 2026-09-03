"""Setting up many models in one sitting.

Every model added from ComfyUI's list starts blank: no family, no numbers, no
name of its own, no trigger words. The model page can fill each one from the
file's own metadata and from CivitAI, one model at a time, which is why none of
forty-five were ever filled and routing had nothing to go on. This pass does
the same work for every model that has not been set up, a few at a time so a
browser can show progress and stop, and records where each answer came from -
the file, the file name, CivitAI's page for the exact file, or the family's
published numbers - because a fill with no provenance is a guess wearing a
label.

Two things it deliberately does not do. It never adopts a CivitAI match that
is not the exact file: a near match is a person's call, and the model's page
offers it. And it never writes a routing card: no lookup knows when a model
should be used, so the report names the models still without one.
"""

from __future__ import annotations

import time

from app.civitai_lookup import family_from_base_model
from app.model_prefill import FAMILY_DEFAULTS, checkpoint_role
from app.repositories import now_ts

BATCH_LIMIT = 20
# Between CivitAI lookups, so a batch of five is a polite trickle rather than a
# burst from one address.
POLITE_PAUSE_SECONDS = 0.25
PROVIDER_DEFAULT = "provider-default"

_FAMILY_SOURCES = {"file": "read from the file", "filename": "guessed from the file name"}


def setup_models(
    catalog, providers, user_id: str, *, limit: int = 5, lookup: bool = False, force: bool = False
) -> dict:
    """Give every uncurated ComfyUI image model its family, numbers and name, and say where each came from."""

    limit = max(1, min(int(limit or 5), BATCH_LIMIT))
    models = [item for item in catalog.catalog(user_id)["resources"] if _is_candidate(item)]
    pending = [item for item in models if force or not (item.get("default_settings") or {}).get("setup")]
    # The lazy pass that gives every model its recipe, so there is one to fill.
    by_model = _recipes_by_model(catalog.presets(user_id, kind="image"))
    processed = []
    for index, model in enumerate(pending[:limit]):
        if lookup and index:
            time.sleep(POLITE_PAUSE_SECONDS)
        processed.append(_setup_one(catalog, providers, user_id, model, by_model.get(model["id"]), lookup=lookup))
    carded = {
        preset["definition"].get("base_model_resource_id")
        for preset in catalog.presets(user_id, kind="image")
        if (preset.get("routing_card") or "").strip()
    }
    # By the names they have now, since the pass may just have renamed some.
    names = {item["id"]: item["name"] for item in models}
    names.update({item["model_id"]: item["name"] for item in processed})
    return {
        "processed": processed,
        "remaining": len(pending) - len(processed),
        "total": len(models),
        "without_routing_card": sorted(name for model_id, name in names.items() if model_id not in carded),
    }


def _is_candidate(item: dict) -> bool:
    return (
        item.get("resource_type") == "model"
        and item.get("kind") == "image"
        and item.get("backend") == "comfyui"
        and bool(item.get("enabled"))
        and item.get("external_id") not in ("", PROVIDER_DEFAULT)
        and checkpoint_role(item.get("external_id") or "") == "base"
    )


def _recipes_by_model(presets: list) -> dict:
    found = {}
    for preset in presets:
        model_id = (preset.get("definition") or {}).get("base_model_resource_id")
        if model_id and model_id not in found:
            found[model_id] = preset
    return found


def _setup_one(catalog, providers, user_id: str, model: dict, preset: dict | None, *, lookup: bool) -> dict:
    filename = str(model["external_id"])
    filled: list[str] = []
    notes: list[str] = []
    settings = dict(model.get("default_settings") or {})

    suggestion = providers.comfyui_model_prefill(user_id, filename)
    family = suggestion.get("family") if suggestion.get("source") in _FAMILY_SOURCES else None
    family_source = _FAMILY_SOURCES.get(str(suggestion.get("source")))

    match, lookup_state = None, "skipped"
    if lookup:
        match, lookup_state, note = _exact_match(providers, filename)
        if note:
            notes.append(note)
    if not family and match and match.get("base_model"):
        family = family_from_base_model(match["base_model"])
        family_source = "CivitAI's page for the file" if family else None

    update = dict(model)
    if family in FAMILY_DEFAULTS and not settings.get("architecture"):
        settings["architecture"] = family
        filled.append(f"family: {FAMILY_DEFAULTS[family]['label']} ({family_source})")
    if match and match.get("model_name") and _is_filename_title(model["name"], filename):
        update["name"] = str(match["model_name"]).strip()[:120]
        filled.append("name (CivitAI)")
    settings["setup"] = {"at": now_ts(), "family": family_source or "unknown", "lookup": lookup_state}
    update["default_settings"] = settings
    saved = catalog.update_resource(user_id, model["id"], update)

    if preset:
        filled.extend(_fill_recipe(catalog, user_id, preset, model, saved, match, family))
    return {
        "model_id": model["id"],
        "file": filename,
        "name": saved["name"],
        "filled": filled,
        "notes": notes,
        "lookup": lookup_state,
        "routing_card": bool((preset or {}).get("routing_card")),
    }


def _exact_match(providers, filename: str) -> tuple[dict | None, str, str]:
    answer = providers.civitai_model_lookup(filename, exact_only=True)
    if not answer.get("ok"):
        return None, "unreachable", str(answer.get("message") or "civitai.com could not be reached.")
    matches = answer.get("matches") or []
    exact = [item for item in matches if item.get("file_match")]
    if exact:
        return exact[0], "exact", ""
    if matches:
        return (
            None,
            "nearest",
            "CivitAI has near matches but not this exact file; pick one on the model's page if one is right.",
        )
    return None, "none", "Nothing on CivitAI matched the file name."


def _is_filename_title(name: str, filename: str) -> bool:
    """Whether the model is still called what the checkpoint list called it."""

    stem = filename.rsplit(".", 1)[0]
    return name.strip() in {filename, stem, stem.replace("_", " ").strip()[:160]}


def _fill_recipe(
    catalog, user_id: str, preset: dict, model: dict, saved: dict, match: dict | None, family
) -> list[str]:
    """Fill a recipe's blanks from the exact match or the family, and say which."""

    filled: list[str] = []
    definition = dict(preset.get("definition") or {})
    sampler = dict(definition.get("sampler") or {})
    dialect = dict(definition.get("prompt_dialect") or {})
    dimensions = list(definition.get("dimensions") or [])
    defaults = FAMILY_DEFAULTS.get(family) if family else None

    if not sampler.get("steps") and not sampler.get("cfg_scale"):
        if match and match.get("steps") is not None:
            sampler.update({key: match[key] for key in ("steps", "cfg_scale") if match.get(key) is not None})
            if match.get("sampler"):
                sampler["sampler_name"] = match["sampler"]
            if match.get("scheduler"):
                sampler["scheduler"] = match["scheduler"]
            source = (
                "the creator's showcase on CivitAI"
                if match.get("settings_source") == "showcase"
                else f"{match.get('family_label') or 'family'} defaults, via CivitAI"
            )
        elif defaults:
            sampler.update(
                {
                    "steps": defaults["steps"],
                    "cfg_scale": defaults["cfg_scale"],
                    "sampler_name": defaults["sampler_name"],
                    "scheduler": defaults["scheduler"],
                }
            )
            source = f"{defaults['label']} family defaults"
        else:
            source = ""
        if source:
            definition["sampler"] = sampler
            filled.append(f"steps and CFG ({source})")
    if not dimensions:
        if match and match.get("width") and match.get("height"):
            definition["dimensions"] = [f"{match['width']}x{match['height']}"]
            filled.append("size (CivitAI)")
        elif defaults:
            definition["dimensions"] = [f"{defaults['width']}x{defaults['height']}"]
            filled.append(f"size ({defaults['label']} family defaults)")
    words = [str(word).strip() for word in (match or {}).get("trigger_words") or [] if str(word).strip()]
    if words:
        joined = ", ".join(words)
        prefix = str(dialect.get("prefix") or "")
        if joined not in prefix:
            dialect["prefix"] = ", ".join(part for part in (joined, prefix) if part)[:300]
            definition["prompt_dialect"] = dialect
            filled.append("trigger words (CivitAI)")

    renamed = saved["name"] != model["name"] and preset.get("name") == model["name"]
    if filled or renamed:
        values = {**preset, "definition": definition}
        if renamed:
            # The recipe follows its model's nickname unless it was renamed on
            # its own, the same rule the model page keeps.
            values["name"] = saved["name"]
        catalog.update_preset(user_id, preset["id"], values)
    return filled
