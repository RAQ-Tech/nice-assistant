"""Pure helpers for the per-generation journal.

The journal records what the platform decided while producing one image or
video so an operator can read an artifact and its journal together. It is
therefore written on a path that handles credentials, provider addresses, and
absolute server paths, and none of those may reach it. Redaction happens here,
once, before anything is persisted - not at render time, so a stored journal is
already safe to export.
"""

from __future__ import annotations

import json
from pathlib import PurePath, PureWindowsPath
import re


JOURNAL_ORIGINS = ("conversation", "direct", "edit", "library")
JOURNAL_STATUSES = ("running", "completed", "failed", "cancelled")
STAGE_STATUSES = ("ok", "skipped", "failed")

MAX_STAGES = 200
MAX_DETAIL_BYTES = 64_000
MAX_STRING_LENGTH = 2_000
MAX_ITEMS = 50
MAX_KEYS = 100
MAX_DEPTH = 8

REDACTED = "[redacted]"

# Substrings that make a key sensitive regardless of nesting. `base_url` is here
# because a provider address is a deployment detail `AGENTS.md` forbids
# publishing; the journal records the backend name instead, which is what a
# reader actually needs.
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "api_auth",
    "authorization",
    "auth_header",
    "password",
    "passphrase",
    "secret",
    "token",
    "credential",
    "base_url",
    "endpoint",
    "master_key",
)

# Keys whose value is a filesystem location. The basename is kept because it
# identifies the artifact; the directory is a user-specific server path.
_PATH_KEY_SUFFIXES = ("_path", "_dir", "_directory")
_PATH_KEY_NAMES = ("path", "local_path", "filename", "file")

_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'=(])((?:[A-Za-z]:[\\/]|/)[^\s\"'()]{2,})")


# Keys that contain a sensitive-looking word and are not secrets. The trigger
# word a technique needs in the prompt is exactly what a journal must show.
_PLAIN_KEYS = frozenset({"required_prompt_token"})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    if lowered in _PLAIN_KEYS:
        return False
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _is_path_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _PATH_KEY_NAMES or lowered.endswith(_PATH_KEY_SUFFIXES)


def basename_only(value: str) -> str:
    """Return the final path component of a location, dropping the directory."""

    text = str(value or "").strip()
    if not text:
        return ""
    candidate = PureWindowsPath(text) if re.match(r"^[A-Za-z]:[\\/]", text) or "\\" in text else PurePath(text)
    name = candidate.name
    return name or text


def _scrub_paths_in_text(value: str) -> str:
    """Reduce absolute paths embedded in free text to their final component."""

    def replace(match: re.Match) -> str:
        whole = match.group(0)
        path = match.group(1)
        return whole.replace(path, basename_only(path))

    return _ABSOLUTE_PATH.sub(replace, value)


def redact(value, *, _depth: int = 0):
    """Return a copy of `value` safe to persist and export.

    Sensitive keys are replaced, filesystem locations are reduced to a basename,
    and the structure is bounded so one pathological detail cannot make a
    journal unreadable.
    """

    if _depth >= MAX_DEPTH:
        return "[truncated: nesting too deep]"
    if isinstance(value, dict):
        result = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_KEYS:
                result["[truncated]"] = f"{len(value) - MAX_KEYS} more keys"
                break
            key = str(raw_key)
            if _is_sensitive_key(key):
                result[key] = REDACTED
            elif _is_path_key(key) and isinstance(item, str):
                result[key] = basename_only(item)
            else:
                result[key] = redact(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        items = list(value)
        result = [redact(item, _depth=_depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            result.append(f"[truncated: {len(items) - MAX_ITEMS} more items]")
        return result
    if isinstance(value, str):
        text = _scrub_paths_in_text(value)
        if len(text) > MAX_STRING_LENGTH:
            return text[:MAX_STRING_LENGTH] + f"… [truncated {len(text) - MAX_STRING_LENGTH} characters]"
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value), _depth=_depth + 1)


def encode_detail(value) -> str:
    """Serialize a redacted stage detail, bounded so one stage cannot dominate."""

    payload = json.dumps(redact(value or {}), separators=(",", ":"), ensure_ascii=False)
    if len(payload.encode("utf-8")) <= MAX_DETAIL_BYTES:
        return payload
    return json.dumps(
        {"truncated": True, "reason": f"stage detail exceeded {MAX_DETAIL_BYTES} bytes"},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def decode_detail(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _duration(milliseconds) -> str:
    try:
        value = int(milliseconds)
    except (TypeError, ValueError):
        return "unknown"
    if value < 1000:
        return f"{value} ms"
    return f"{value / 1000:.2f} s"


def render_export(journal: dict) -> str:
    """Render a journal as one self-contained Markdown document.

    Markdown rather than JSON because the point of the export is that a person
    can read it, and paste it somewhere, without tooling.
    """

    lines = [
        f"# Generation journal {journal.get('id') or ''}".strip(),
        "",
        "Produced by Nice Assistant. Credentials, provider addresses, and server",
        "paths are removed; file locations appear as names only.",
        "",
        "## Summary",
        "",
        f"- Status: {journal.get('status') or 'unknown'}",
        f"- Kind: {journal.get('kind') or 'unknown'}",
        f"- Origin: {journal.get('origin') or 'unknown'}",
        f"- Started: {journal.get('started_at') or 'unknown'}",
        f"- Completed: {journal.get('completed_at') or 'not recorded'}",
        f"- Duration: {_duration(journal.get('duration_ms'))}",
    ]
    if journal.get("media_id"):
        lines.append(f"- Media: {journal['media_id']}")
    if journal.get("media_plan_id"):
        lines.append(f"- Plan: {journal['media_plan_id']}")
    if journal.get("persona_id"):
        lines.append(f"- Persona: {journal['persona_id']}")
    error = journal.get("error") or {}
    if error:
        lines.extend(
            [
                "",
                "## Failure",
                "",
                f"- Code: {error.get('code') or 'unknown'}",
                f"- Message: {error.get('message') or ''}",
            ]
        )
    lines.extend(["", "## Stages", ""])
    stages = journal.get("stages") or []
    if not stages:
        lines.append("No stages were recorded.")
    for stage in stages:
        lines.append(f"### {stage.get('sequence')}. {stage.get('stage') or 'stage'} - {stage.get('status') or ''}")
        lines.append("")
        if stage.get("summary"):
            lines.extend([str(stage["summary"]), ""])
        lines.append(f"Duration: {_duration(stage.get('duration_ms'))}")
        detail = stage.get("detail") or {}
        if detail:
            lines.extend(
                [
                    "",
                    "```json",
                    json.dumps(detail, indent=2, ensure_ascii=False, sort_keys=True),
                    "```",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
