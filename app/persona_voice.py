"""A persona's voice settings, keyed by provider rather than by column.

A persona used to carry nine text-to-speech columns, six of which had a provider
name in them. Adding a third provider meant three more columns and a migration,
and a provider nobody had added a column for resolved silently to nothing.

Here a persona holds one object: providers it has an opinion about, plus an
optional `default` that any provider falls back to. Nothing in this module knows
which providers exist, which is the point - a new one needs no schema change and
no code here.
"""

from __future__ import annotations

import json


FIELDS = ("voice", "model", "speed")
DEFAULT_KEY = "default"
MAX_PROVIDERS = 8


def normalize(values) -> dict:
    """Clean a submitted set of voice preferences.

    Provider keys are kept as given, lowercased. An entry with nothing in it is
    dropped rather than stored, so "has an opinion about this provider" and "was
    once opened and left blank" do not look the same later.
    """

    if not isinstance(values, dict):
        return {}
    preferences = {}
    for raw_provider, entry in list(values.items())[:MAX_PROVIDERS]:
        provider = str(raw_provider or "").strip().lower()[:40]
        if not provider or not isinstance(entry, dict):
            continue
        cleaned = {}
        for field in FIELDS:
            text = " ".join(str(entry.get(field) or "").split()).strip()
            if text:
                cleaned[field] = text[:120]
        if cleaned:
            preferences[provider] = cleaned
    return preferences


def parse(raw) -> dict:
    try:
        return normalize(json.loads(raw or "{}"))
    except (TypeError, ValueError):
        return {}


def dump(values) -> str:
    return json.dumps(normalize(values), separators=(",", ":"), ensure_ascii=False)


def preference(preferences: dict, provider: str, field: str) -> str:
    """What this persona wants for one provider, or nothing.

    Falls back to `default`, which is what an older persona's unqualified
    setting became: it was chosen under whichever provider was configured at the
    time, so it is a preference without a provider rather than a preference for
    one.
    """

    if field not in FIELDS:
        return ""
    values = preferences if isinstance(preferences, dict) else {}
    for key in (str(provider or "").strip().lower(), DEFAULT_KEY):
        entry = values.get(key)
        if isinstance(entry, dict) and entry.get(field):
            return str(entry[field])
    return ""
