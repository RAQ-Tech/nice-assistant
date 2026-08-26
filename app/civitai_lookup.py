"""Turning a CivitAI search answer into reviewable model suggestions.

The strongest lookup key would be the file's fingerprint, but the app cannot
read model files over the network, so the search runs on the filename and a
person picks the right match. Everything here is parsing: the HTTP call lives
with the other provider calls, and these functions never guess silently - an
exact filename match is marked, and settings come from the creator's own
showcase images or not at all.
"""

from __future__ import annotations

from collections import Counter

# CivitAI publishes A1111-style sampler names; ComfyUI wants its own identifiers
# plus a scheduler. Unmapped names pass through untranslated so the page can
# show them rather than dropping them.
SAMPLER_MAP: dict[str, tuple[str, str]] = {
    "euler a": ("euler_ancestral", "normal"),
    "euler": ("euler", "normal"),
    "heun": ("heun", "normal"),
    "lms": ("lms", "normal"),
    "ddim": ("ddim", "normal"),
    "uni_pc": ("uni_pc", "normal"),
    "unipc": ("uni_pc", "normal"),
    "dpm++ 2m": ("dpmpp_2m", "normal"),
    "dpm++ 2m karras": ("dpmpp_2m", "karras"),
    "dpm++ 2m sde": ("dpmpp_2m_sde", "normal"),
    "dpm++ 2m sde karras": ("dpmpp_2m_sde", "karras"),
    "dpm++ sde": ("dpmpp_sde", "normal"),
    "dpm++ sde karras": ("dpmpp_sde", "karras"),
    "dpm++ 3m sde": ("dpmpp_3m_sde", "normal"),
    "dpm++ 3m sde karras": ("dpmpp_3m_sde", "karras"),
}


def search_query(checkpoint: str) -> str:
    """The filename as words: the stem, separators opened up."""

    stem = checkpoint.rsplit(".", 1)[0]
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def translate_sampler(name: str) -> tuple[str, str | None]:
    mapped = SAMPLER_MAP.get(str(name).strip().lower())
    return mapped if mapped else (str(name), None)


def _showcase_settings(images: list) -> dict:
    """The most common generation settings across a version's showcase images.

    Creators publish images with their generation info attached; the mode of
    those values is the closest thing to "settings the creator actually used"
    the API offers. Images without metadata simply do not vote.
    """

    def votes(*keys: str) -> Counter:
        counted: Counter = Counter()
        for image in images:
            meta = image.get("meta") if isinstance(image, dict) else None
            if not isinstance(meta, dict):
                continue
            for key in keys:
                value = meta.get(key)
                if value not in (None, ""):
                    counted[str(value)] += 1
                    break
        return counted

    result: dict = {}
    steps = votes("steps", "Steps")
    if steps:
        try:
            result["steps"] = int(float(steps.most_common(1)[0][0]))
        except ValueError:
            pass
    cfg = votes("cfgScale", "cfg_scale", "CFG scale")
    if cfg:
        try:
            result["cfg_scale"] = float(cfg.most_common(1)[0][0])
        except ValueError:
            pass
    sampler = votes("sampler", "Sampler")
    if sampler:
        name, scheduler = translate_sampler(sampler.most_common(1)[0][0])
        result["sampler"] = name
        if scheduler:
            result["scheduler"] = scheduler
    size = votes("Size", "size")
    if size:
        parts = size.most_common(1)[0][0].lower().split("x")
        if len(parts) == 2 and all(part.strip().isdigit() for part in parts):
            result["width"] = int(parts[0])
            result["height"] = int(parts[1])
    return result


def parse_matches(payload: dict, checkpoint: str, limit: int = 5) -> list[dict]:
    """Reviewable matches, exact filename matches first."""

    matches: list[dict] = []
    items = payload.get("items")
    for model in items if isinstance(items, list) else []:
        if not isinstance(model, dict):
            continue
        for version in model.get("modelVersions") or []:
            if not isinstance(version, dict):
                continue
            files = [entry.get("name") for entry in version.get("files") or [] if isinstance(entry, dict)]
            file_match = checkpoint in files
            trigger_words = [str(word) for word in version.get("trainedWords") or [] if str(word).strip()]
            entry = {
                "model_name": str(model.get("name") or "").strip(),
                "version_name": str(version.get("name") or "").strip(),
                "base_model": str(version.get("baseModel") or "").strip(),
                "file_match": file_match,
                "trigger_words": trigger_words,
                "url": f"https://civitai.com/models/{model.get('id')}?modelVersionId={version.get('id')}",
                **_showcase_settings(version.get("images") or []),
            }
            if entry["model_name"]:
                matches.append(entry)
    matches.sort(key=lambda item: not item["file_match"])
    return matches[:limit]
