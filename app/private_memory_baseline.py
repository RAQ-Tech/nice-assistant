"""Offline, snapshot-only memory baseline export and reset-safety drill.

This module is intentionally not registered with the web application.  It reads
only Nice Assistant snapshot ZIPs, extracts the database into an internally
managed temporary directory, and never exposes a live-reset entrypoint.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
from typing import Iterator
import unicodedata
import zipfile

from app.chat import persona_instruction_block


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CURRENT_SCHEMA_REVISION = "0019_memory_v3_identity_access"
EXPORT_FORMAT = "nice-assistant-private-memory-baseline"
EXPORT_FORMAT_VERSION = 2
MAX_BASELINE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DATABASE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024 * 1024
MAX_IDENTITY_REFERENCE_ASSET_BYTES = 2 * 1024 * 1024 * 1024
MAX_IDENTITY_REFERENCE_TOTAL_BYTES = 32 * 1024 * 1024 * 1024
MIN_EXTRACTION_HEADROOM_BYTES = 64 * 1024 * 1024
REQUIRED_TABLES = {
    "alembic_version",
    "users",
    "workspaces",
    "personas",
    "persona_workspace_links",
    "human_principals",
    "owner_profiles",
    "owner_profile_events",
    "chats",
    "chat_bindings",
    "messages",
    "conversation_turns",
    "memories",
    "memory_events",
    "memory_records",
    "memory_origins",
    "memory_grants",
    "memory_grant_events",
    "memory_fts",
    "async_jobs",
    "task_model_runs",
    "media_files",
    "persona_visual_identities",
    "persona_identity_references",
}
REQUIRED_COLUMNS = {
    "alembic_version": {"version_num"},
    "users": {"id", "username", "is_admin", "created_at"},
    "workspaces": {"id", "user_id", "name", "created_at"},
    "personas": {
        "id",
        "workspace_id",
        "name",
        "system_prompt",
        "personality_details",
        "traits_json",
        "default_model",
        "allow_image_sends",
        "created_at",
    },
    "persona_workspace_links": {"persona_id", "workspace_id"},
    "human_principals": {"id", "user_id", "created_at", "updated_at"},
    "owner_profiles": {"human_id", "revision", "created_at", "updated_at"},
    "owner_profile_events": {"id", "human_id", "action", "changed_fields_json", "created_at"},
    "chats": {"id", "user_id", "workspace_id", "persona_id", "title"},
    "chat_bindings": {
        "chat_id",
        "human_id",
        "persona_id",
        "context_kind",
        "workspace_id",
        "binding_status",
        "persona_name_snapshot",
        "workspace_name_snapshot",
        "created_at",
    },
    "messages": {"id", "chat_id", "role", "text", "created_at"},
    "conversation_turns": {"id", "user_id", "chat_id"},
    "media_files": {"id", "user_id"},
    "memories": {
        "id",
        "user_id",
        "tier",
        "tier_ref_id",
        "content",
        "normalized_content",
        "status",
        "source_type",
        "source_message_id",
        "source_turn_id",
        "confidence",
        "supersedes_id",
        "extractor_provider",
        "extractor_model",
        "extractor_version",
        "created_at",
        "updated_at",
        "reviewed_at",
        "forgotten_at",
    },
    "memory_events": {
        "id",
        "user_id",
        "memory_id",
        "related_memory_id",
        "action",
        "from_status",
        "to_status",
        "snapshot_json",
        "created_at",
        "undone_at",
    },
    "memory_records": {
        "memory_id",
        "human_id",
        "lineage",
        "access_state",
        "memory_type",
        "validity_status",
        "valid_until",
        "stateful_status",
        "last_confirmed_at",
        "created_at",
        "updated_at",
    },
    "memory_origins": {
        "memory_id",
        "human_id",
        "source_kind",
        "source_chat_id",
        "source_persona_id",
        "source_workspace_id",
        "source_message_id",
        "source_turn_id",
        "evidence_json",
        "provenance_status",
        "revision_of_memory_id",
        "created_at",
    },
    "memory_grants": {
        "id",
        "memory_id",
        "human_id",
        "grant_type",
        "persona_id",
        "workspace_id",
        "grant_source",
        "granted_by_human_id",
        "granted_at",
        "revoked_by_human_id",
        "revoked_at",
    },
    "memory_grant_events": {
        "id",
        "memory_id",
        "grant_id",
        "human_id",
        "action",
        "grant_type",
        "target_id",
        "created_at",
    },
    "memory_fts": {"memory_id", "user_id", "content"},
    "async_jobs": {
        "id",
        "user_id",
        "chat_id",
        "kind",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "result_json",
        "error",
    },
    "task_model_runs": {
        "id",
        "user_id",
        "role",
        "chat_id",
        "turn_id",
        "requested_provider",
        "requested_model",
        "executed_provider",
        "executed_model",
        "status",
        "fallback_used",
        "error_code",
        "error_message",
        "attempts_json",
        "input_tokens_estimated",
        "output_tokens_estimated",
        "latency_ms",
        "started_at",
        "completed_at",
    },
    "persona_visual_identities": {"id", "user_id", "persona_id"},
    "persona_identity_references": {
        "id",
        "user_id",
        "persona_id",
        "filename",
        "sha256",
        "review_status",
    },
}
MEMORY_TABLES = {
    "memories",
    "memory_events",
    "memory_records",
    "memory_origins",
    "memory_grants",
    "memory_grant_events",
}
MEMORY_FTS_PREFIX = "memory_fts"
RAW_PERSONA_TABLES = (
    "workspaces",
    "personas",
    "persona_workspace_links",
    "persona_visual_identities",
    "persona_identity_references",
)
UNAVAILABLE_V2_FIELDS = {
    "qualification_reason": "Memory v2 never stored a qualification rationale.",
    "evidence_spans": "Memory v2 never stored supporting source spans.",
    "raw_extractor_output": "Memory v2 never stored the extractor's raw response.",
    "extractor_decision_trace": "Memory v2 never stored model reasoning or a decision trace.",
    "valid_until": "Memory v2 has no temporal-validity field.",
    "last_confirmed_at": "Memory v2 has no last-confirmed field.",
    "lifecycle_state": "Memory v2 has no durable/temporal/stateful lifecycle field.",
    "grants": "Memory v2 stores one scope rather than origin plus access grants.",
}
DIRECTIVE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:assistant|persona|you|your|system\s*prompt|personality|character|tone|voice)\b"
    r".{0,100}\b(?:always|never|must|should|speak|respond|reply|act|behave|sound|address|call|refer|use|avoid)\b"
    r"|"
    r"\b(?:always|never|must|should|speak|respond|reply|act|behave|sound|address|call|refer|use|avoid)\b"
    r".{0,100}\b(?:assistant|persona|you|your|tone|voice|user)\b"
    r")",
    re.I | re.S,
)
DIRECTIVE_VERB_PATTERN = re.compile(
    r"\b(?:always|never|must|should|speak|respond|reply|act|behave|sound|address|call|refer|use|avoid)\b",
    re.I,
)
IMPERATIVE_INSTRUCTION_PATTERN = re.compile(
    r"(?:^|[.!?]\s*)(?:always\s+|never\s+)?"
    r"(?:respond|reply|speak|behave|act|use|avoid|address|call|refer|write|answer|format|keep|be)\b",
    re.I,
)
PERSONA_DESCRIPTION_PATTERN = re.compile(
    r"\b(?:is|are|was|were|has|acts|sounds|speaks|responds|uses|prefers)\b",
    re.I,
)
DEFINITION_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "always",
    "and",
    "are",
    "assistant",
    "been",
    "before",
    "being",
    "but",
    "can",
    "character",
    "for",
    "from",
    "have",
    "into",
    "never",
    "not",
    "persona",
    "personality",
    "should",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "tone",
    "use",
    "voice",
    "was",
    "were",
    "with",
    "you",
    "your",
}


class BaselineError(RuntimeError):
    """A safe, operator-facing baseline or drill failure."""


@dataclass(frozen=True)
class SnapshotDatabase:
    snapshot_path: Path
    database_path: Path
    snapshot_sha256: str
    snapshot_size: int
    snapshot_mtime_ns: int
    database_sha256: str
    manifest: dict
    archive_names: tuple[str, ...]
    schema_revision: str


@dataclass(frozen=True)
class BaselineExportResult:
    json_path: Path
    text_path: Path
    baseline_sha256: str
    reset_plan_sha256: str
    memory_count: int
    quarantine_count: int
    delete_count: int
    owner_id: str
    permission_verification: dict

    def content_free_response(self) -> dict:
        return {
            "ok": True,
            "baseline_sha256": self.baseline_sha256,
            "reset_plan_sha256": self.reset_plan_sha256,
            "memory_count": self.memory_count,
            "quarantine_count": self.quarantine_count,
            "delete_count": self.delete_count,
            "permission_verification": self.permission_verification,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value):
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _quoted_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> list[dict]:
    rows = connection.execute(f"PRAGMA table_info({_quoted_identifier(table)})").fetchall()
    return [
        {
            "cid": int(row[0]),
            "name": str(row[1]),
            "type": str(row[2] or ""),
            "not_null": bool(row[3]),
            "default": _json_value(row[4]),
            "primary_key_order": int(row[5]),
        }
        for row in rows
    ]


def _rows(
    connection: sqlite3.Connection,
    table: str,
    *,
    where: str | None = None,
    parameters: tuple = (),
) -> list[dict]:
    sql = f"SELECT * FROM {_quoted_identifier(table)}"
    if where:
        sql += f" WHERE {where}"
    column_names = [item["name"] for item in _table_columns(connection, table)]
    values = []
    for row in connection.execute(sql, parameters).fetchall():
        values.append({name: _json_value(row[index]) for index, name in enumerate(column_names)})
    values.sort(key=_canonical_bytes)
    return values


def _schema_sql(connection: sqlite3.Connection, table: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _table_hash(connection: sqlite3.Connection, table: str) -> dict:
    rows = _rows(connection, table)
    payload = {
        "table": table,
        "schema_sql": _schema_sql(connection, table),
        "columns": _table_columns(connection, table),
        "rows": rows,
    }
    return {
        "row_count": len(rows),
        "sha256": _canonical_sha256(payload),
        "schema_sha256": _canonical_sha256(
            {
                "schema_sql": payload["schema_sql"],
                "columns": payload["columns"],
            }
        ),
    }


def _protected_non_memory_hashes(connection: sqlite3.Connection) -> dict:
    protected = {}
    for table in sorted(_table_names(connection)):
        if table in MEMORY_TABLES or table.startswith(MEMORY_FTS_PREFIX):
            continue
        protected[table] = _table_hash(connection, table)
    return {
        "tables": protected,
        "sha256": _canonical_sha256(protected),
    }


def _foreign_key_inventory(connection: sqlite3.Connection) -> dict:
    inventory = []
    for table in sorted(_table_names(connection)):
        for row in connection.execute(f"PRAGMA foreign_key_list({_quoted_identifier(table)})").fetchall():
            inventory.append(
                {
                    "table": table,
                    "id": int(row[0]),
                    "sequence": int(row[1]),
                    "references_table": str(row[2]),
                    "from_column": str(row[3]),
                    "to_column": str(row[4]),
                    "on_update": str(row[5]),
                    "on_delete": str(row[6]),
                    "match": str(row[7]),
                }
            )
    inventory.sort(key=_canonical_bytes)
    return {
        "items": inventory,
        "sha256": _canonical_sha256(inventory),
    }


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict, tuple[str, ...]]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise BaselineError("Snapshot contains too many archive members.")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise BaselineError("Snapshot contains duplicate archive members.")
    for info in infos:
        name = info.filename
        if not name or "\x00" in name or "\\" in name:
            raise BaselineError("Snapshot contains an unsafe archive path.")
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts:
            raise BaselineError("Snapshot contains an unsafe archive path.")
        file_mode = (info.external_attr >> 16) & 0o170000
        if file_mode == stat.S_IFLNK:
            raise BaselineError("Snapshot contains a symbolic-link archive member.")
    if "manifest.json" not in names or "nice_assistant.db" not in names:
        raise BaselineError("Snapshot is missing its manifest or database.")
    manifest_info = archive.getinfo("manifest.json")
    if manifest_info.is_dir() or not 0 < manifest_info.file_size <= MAX_MANIFEST_BYTES:
        raise BaselineError("Snapshot manifest size is invalid.")
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise BaselineError("Snapshot manifest is invalid.") from exc
    if not isinstance(manifest, dict):
        raise BaselineError("Snapshot manifest is invalid.")
    format_version = manifest.get("formatVersion")
    if isinstance(format_version, bool) or not isinstance(format_version, int):
        raise BaselineError("Snapshot manifest has an invalid format version.")
    if (
        manifest.get("app") != "nice-assistant"
        or manifest.get("database") != "nice_assistant.db"
        or format_version != 1
    ):
        raise BaselineError("Snapshot is not a supported Nice Assistant snapshot.")
    entry_count = manifest.get("entryCount")
    if isinstance(entry_count, bool) or not isinstance(entry_count, int):
        raise BaselineError("Snapshot manifest has an invalid entry count.")
    if entry_count != len(infos):
        raise BaselineError("Snapshot manifest entry count does not match the archive.")
    include_media = manifest.get("includeMedia")
    media_dirs = manifest.get("mediaDirs")
    if not isinstance(include_media, bool) or not isinstance(media_dirs, list):
        raise BaselineError("Snapshot manifest has invalid media metadata.")
    if any(not isinstance(value, str) or not value or "/" in value or "\\" in value for value in media_dirs):
        raise BaselineError("Snapshot manifest has invalid media directory metadata.")
    data_members = [name for name in names if name.startswith("data/") and not name.endswith("/")]
    if not include_media and (media_dirs or data_members):
        raise BaselineError("Metadata-only snapshot unexpectedly contains media metadata or files.")
    if include_media:
        allowed = set(media_dirs)
        for name in data_members:
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[1] not in allowed:
                raise BaselineError("Snapshot media member is inconsistent with its manifest.")
    return manifest, tuple(sorted(names))


def _validate_database(connection: sqlite3.Connection) -> str:
    tables = _table_names(connection)
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise BaselineError("Snapshot database is missing required current-schema tables.")
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            raise BaselineError("Snapshot database is missing a required current-schema table.")
        actual = {column["name"] for column in _table_columns(connection, table)}
        if not required.issubset(actual):
            raise BaselineError("Snapshot database is missing required current-schema columns.")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if not integrity or str(integrity[0]) != "ok":
        raise BaselineError("Snapshot database integrity check failed.")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise BaselineError("Snapshot database foreign-key check failed.")
    revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(revisions) != 1:
        raise BaselineError("Snapshot database has an invalid migration revision record.")
    revision = str(revisions[0][0])
    if revision != CURRENT_SCHEMA_REVISION:
        raise BaselineError(
            f"Snapshot schema revision {revision!r} is not the required current revision "
            f"{CURRENT_SCHEMA_REVISION!r}; the baseline tool does not migrate snapshots."
        )
    return revision


@contextmanager
def extracted_snapshot_database(snapshot) -> Iterator[SnapshotDatabase]:
    """Yield a validated database extracted into a tool-owned temporary directory."""

    snapshot_path = Path(snapshot).expanduser().resolve()
    if snapshot_path.suffix.lower() != ".zip":
        raise BaselineError("Input must be an existing Nice Assistant snapshot ZIP.")
    with tempfile.TemporaryDirectory(prefix="nice-assistant-memory-baseline-") as temporary:
        immutable_snapshot_path = Path(temporary) / "source_snapshot.zip"
        database_path = Path(temporary) / "nice_assistant.db"
        before = None
        snapshot_sha256 = None
        try:
            with snapshot_path.open("rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                    raise BaselineError("Input must be an existing Nice Assistant snapshot ZIP.")
                copy_headroom = max(
                    MIN_EXTRACTION_HEADROOM_BYTES,
                    int(before.st_size) // 20,
                )
                if shutil.disk_usage(temporary).free < int(before.st_size) + copy_headroom:
                    raise BaselineError("Insufficient temporary free space for an immutable snapshot copy.")
                with immutable_snapshot_path.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
            if immutable_snapshot_path.stat().st_size != int(before.st_size):
                raise BaselineError("Snapshot changed while creating its immutable inspection copy.")
            try:
                os.chmod(immutable_snapshot_path, 0o600)
            except OSError:
                pass
            snapshot_sha256 = _sha256_file(immutable_snapshot_path)

            with zipfile.ZipFile(immutable_snapshot_path, "r") as archive:
                manifest, archive_names = _read_manifest(archive)
                database_info = archive.getinfo("nice_assistant.db")
                if database_info.is_dir() or database_info.file_size <= 0:
                    raise BaselineError("Snapshot database artifact is empty.")
                if database_info.file_size > MAX_DATABASE_UNCOMPRESSED_BYTES:
                    raise BaselineError("Snapshot database artifact exceeds the offline safety limit.")
                required_space = database_info.file_size + max(
                    MIN_EXTRACTION_HEADROOM_BYTES,
                    database_info.file_size // 20,
                )
                if shutil.disk_usage(temporary).free < required_space:
                    raise BaselineError("Insufficient temporary free space for safe database inspection.")
                with archive.open(database_info, "r") as source, database_path.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                if database_path.stat().st_size != database_info.file_size:
                    raise BaselineError("Snapshot database extraction was incomplete.")
            try:
                os.chmod(database_path, 0o600)
            except OSError:
                pass
            try:
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("PRAGMA query_only=ON")
                    revision = _validate_database(connection)
                finally:
                    connection.close()
            except sqlite3.Error as exc:
                raise BaselineError("Snapshot database could not be safely inspected.") from exc
            yield SnapshotDatabase(
                snapshot_path=immutable_snapshot_path,
                database_path=database_path,
                snapshot_sha256=snapshot_sha256,
                snapshot_size=int(before.st_size),
                snapshot_mtime_ns=int(before.st_mtime_ns),
                database_sha256=_sha256_file(database_path),
                manifest=manifest,
                archive_names=archive_names,
                schema_revision=revision,
            )
        except BaselineError:
            raise
        except zipfile.BadZipFile as exc:
            raise BaselineError("Snapshot ZIP is corrupt.") from exc
        except (EOFError, MemoryError, OSError, RuntimeError, sqlite3.Error, zipfile.LargeZipFile) as exc:
            raise BaselineError("Snapshot could not be safely inspected.") from exc
        finally:
            if before is not None and snapshot_sha256 is not None:
                try:
                    after = snapshot_path.stat()
                    current_sha256 = _sha256_file(snapshot_path)
                except OSError as exc:
                    raise BaselineError("Snapshot changed or disappeared during inspection.") from exc
                if (
                    int(after.st_size) != int(before.st_size)
                    or int(after.st_mtime_ns) != int(before.st_mtime_ns)
                    or current_sha256 != snapshot_sha256
                ):
                    raise BaselineError("Snapshot changed during inspection.")


def _owner_record(connection: sqlite3.Connection, owner_id: str | None) -> dict:
    users = connection.execute("SELECT id,username,is_admin,created_at FROM users ORDER BY id").fetchall()
    if not users:
        raise BaselineError("Snapshot has no owner account.")
    if owner_id is None:
        if len(users) != 1:
            raise BaselineError("Snapshot has multiple accounts; --owner-id is required.")
        selected = users[0]
    else:
        selected = next((row for row in users if str(row[0]) == str(owner_id)), None)
        if selected is None:
            raise BaselineError("The requested owner ID is not present in the snapshot.")
    return {
        "id": str(selected[0]),
        "username": str(selected[1]),
        "is_admin": bool(selected[2]),
        "created_at": int(selected[3]),
    }


def _rows_by_id(rows: list[dict]) -> dict[str, dict]:
    return {str(row["id"]): row for row in rows if row.get("id") is not None}


def _safe_json_object(value) -> dict | list | None:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _validate_v3_reference_ownership(
    connection: sqlite3.Connection,
    owner_id: str,
    memories: list[dict],
    memory_v3: dict[str, list[dict]],
) -> None:
    workspace_owners = {
        str(row[0]): str(row[1]) for row in connection.execute("SELECT id,user_id FROM workspaces").fetchall()
    }
    persona_owners = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT p.id,w.user_id FROM personas p JOIN workspaces w ON w.id=p.workspace_id"
        ).fetchall()
    }
    chat_owners = {str(row[0]): str(row[1]) for row in connection.execute("SELECT id,user_id FROM chats").fetchall()}
    message_owners = {
        str(row[0]): str(row[1])
        for row in connection.execute("SELECT m.id,c.user_id FROM messages m JOIN chats c ON c.id=m.chat_id").fetchall()
    }
    turn_owners = {
        str(row[0]): str(row[1]) for row in connection.execute("SELECT id,user_id FROM conversation_turns").fetchall()
    }
    memory_owners = {
        str(row[0]): str(row[1]) for row in connection.execute("SELECT id,user_id FROM memories").fetchall()
    }

    def reject_other_owner(value, owners: dict[str, str], label: str) -> None:
        if value is None:
            return
        referenced_owner = owners.get(str(value))
        if referenced_owner is not None and referenced_owner != owner_id:
            raise BaselineError(f"Memory v3 {label} crosses owner boundaries in the snapshot.")

    for memory in memories:
        reject_other_owner(
            memory.get("source_message_id"),
            message_owners,
            "legacy source message",
        )
        reject_other_owner(
            memory.get("source_turn_id"),
            turn_owners,
            "legacy source turn",
        )
        tier_owners = {
            "workspace": workspace_owners,
            "persona": persona_owners,
            "chat": chat_owners,
        }.get(str(memory.get("tier") or ""))
        if tier_owners is not None:
            reject_other_owner(
                memory.get("tier_ref_id"),
                tier_owners,
                "legacy scope target",
            )

    for origin in memory_v3["memory_origins"]:
        reject_other_owner(
            origin.get("source_chat_id"),
            chat_owners,
            "source chat",
        )
        reject_other_owner(
            origin.get("source_persona_id"),
            persona_owners,
            "source persona",
        )
        reject_other_owner(
            origin.get("source_workspace_id"),
            workspace_owners,
            "source workspace",
        )
        reject_other_owner(
            origin.get("source_message_id"),
            message_owners,
            "source message",
        )
        reject_other_owner(
            origin.get("source_turn_id"),
            turn_owners,
            "source turn",
        )
        reject_other_owner(
            origin.get("revision_of_memory_id"),
            memory_owners,
            "revision",
        )
    for grant in memory_v3["memory_grants"]:
        target_owners = persona_owners if grant.get("grant_type") == "persona" else workspace_owners
        reject_other_owner(
            grant.get("persona_id") or grant.get("workspace_id"),
            target_owners,
            "grant target",
        )
    for event in memory_v3["memory_grant_events"]:
        target_owners = persona_owners if event.get("grant_type") == "persona" else workspace_owners
        reject_other_owner(
            event.get("target_id"),
            target_owners,
            "grant-event target",
        )


def _source_and_task_resolution(
    connection: sqlite3.Connection,
    owner_id: str,
    memories: list[dict],
    memory_v3: dict[str, list[dict]],
) -> tuple[list[dict], dict]:
    memory_ids = {str(row["id"]) for row in memories}
    records_by_memory = {str(row["memory_id"]): row for row in memory_v3["memory_records"]}
    origins_by_memory = {str(row["memory_id"]): row for row in memory_v3["memory_origins"]}
    grants_by_memory: dict[str, list[dict]] = {memory_id: [] for memory_id in memory_ids}
    for grant in memory_v3["memory_grants"]:
        grants_by_memory.setdefault(str(grant["memory_id"]), []).append(grant)
    grant_events_by_memory: dict[str, list[dict]] = {memory_id: [] for memory_id in memory_ids}
    for event in memory_v3["memory_grant_events"]:
        grant_events_by_memory.setdefault(str(event["memory_id"]), []).append(event)
    for values in grants_by_memory.values():
        values.sort(
            key=lambda item: (
                int(item.get("granted_at") or 0),
                str(item.get("id") or ""),
            )
        )
    for values in grant_events_by_memory.values():
        values.sort(
            key=lambda item: (
                int(item.get("created_at") or 0),
                str(item.get("id") or ""),
            )
        )
    if any(memory.get("supersedes_id") and str(memory["supersedes_id"]) not in memory_ids for memory in memories):
        raise BaselineError("Memory revision ownership is inconsistent in the snapshot.")
    owner_events = _rows(
        connection,
        "memory_events",
        where="user_id=?",
        parameters=(owner_id,),
    )
    if any(str(event.get("memory_id") or "") not in memory_ids for event in owner_events):
        raise BaselineError("Memory event ownership is inconsistent in the snapshot.")
    if any(
        event.get("related_memory_id") and str(event["related_memory_id"]) not in memory_ids for event in owner_events
    ):
        raise BaselineError("Memory event revision ownership is inconsistent in the snapshot.")
    if memory_ids:
        memory_placeholders = ",".join("?" for _ in memory_ids)
        events = _rows(
            connection,
            "memory_events",
            where=f"memory_id IN ({memory_placeholders})",
            parameters=tuple(sorted(memory_ids)),
        )
    else:
        events = []
    if any(str(event.get("user_id") or "") != owner_id for event in events):
        raise BaselineError("Memory event ownership is inconsistent in the snapshot.")
    if {str(event["id"]) for event in events} != {str(event["id"]) for event in owner_events}:
        raise BaselineError("Memory event ownership is inconsistent in the snapshot.")
    events_by_memory: dict[str, list[dict]] = {memory_id: [] for memory_id in memory_ids}
    for event in events:
        events_by_memory.setdefault(str(event["memory_id"]), []).append(event)

    source_message_ids = sorted(
        {
            str(row["source_message_id"])
            for row in [*memories, *origins_by_memory.values()]
            if row.get("source_message_id")
        }
    )
    source_turn_ids = sorted(
        {str(row["source_turn_id"]) for row in [*memories, *origins_by_memory.values()] if row.get("source_turn_id")}
    )
    messages = {}
    turns = {}
    if source_message_ids:
        placeholders = ",".join("?" for _ in source_message_ids)
        columns = [item["name"] for item in _table_columns(connection, "messages")]
        rows = connection.execute(
            f"SELECT m.* FROM messages m JOIN chats c ON c.id=m.chat_id WHERE c.user_id=? AND m.id IN ({placeholders})",
            (owner_id, *source_message_ids),
        ).fetchall()
        messages = _rows_by_id([{name: _json_value(row[index]) for index, name in enumerate(columns)} for row in rows])
    if source_turn_ids:
        placeholders = ",".join("?" for _ in source_turn_ids)
        turns = _rows_by_id(
            _rows(
                connection,
                "conversation_turns",
                where=f"user_id=? AND id IN ({placeholders})",
                parameters=(owner_id, *source_turn_ids),
            )
        )

    all_chats = _rows_by_id(_rows(connection, "chats", where="user_id=?", parameters=(owner_id,)))
    workspace_rows = _rows_by_id(_rows(connection, "workspaces", where="user_id=?", parameters=(owner_id,)))
    owner_workspace_ids = set(workspace_rows)
    persona_rows = _rows_by_id(
        [row for row in _rows(connection, "personas") if str(row.get("workspace_id") or "") in owner_workspace_ids]
    )
    for link in _rows(connection, "persona_workspace_links"):
        if str(link.get("workspace_id") or "") in owner_workspace_ids:
            persona_id = str(link.get("persona_id") or "")
            if persona_id and persona_id not in persona_rows:
                row = connection.execute("SELECT * FROM personas WHERE id=?", (persona_id,)).fetchone()
                if row:
                    columns = [item["name"] for item in _table_columns(connection, "personas")]
                    persona_rows[persona_id] = {name: _json_value(row[index]) for index, name in enumerate(columns)}

    jobs = _rows(
        connection,
        "async_jobs",
        where="user_id=? AND kind='memory_extraction'",
        parameters=(owner_id,),
    )
    candidate_job: dict[str, dict] = {}
    task_run_ids = set()
    for job in jobs:
        if job.get("chat_id") and str(job["chat_id"]) not in all_chats:
            raise BaselineError("Memory extraction job ownership is inconsistent in the snapshot.")
        result = _safe_json_object(job.get("result_json"))
        if not isinstance(result, dict):
            continue
        task_run_id = result.get("task_run_id")
        if task_run_id:
            task_run_ids.add(str(task_run_id))
        for memory_id in result.get("candidate_ids") or []:
            memory_id = str(memory_id)
            if memory_id in memory_ids:
                candidate_job[memory_id] = {
                    "match_method": "extraction_job_candidate_ids",
                    "job": {
                        key: job.get(key)
                        for key in (
                            "id",
                            "chat_id",
                            "status",
                            "created_at",
                            "started_at",
                            "completed_at",
                            "error",
                        )
                    },
                    "task_run_id": str(task_run_id) if task_run_id else None,
                }
    task_runs = {}
    if task_run_ids:
        values = sorted(task_run_ids)
        placeholders = ",".join("?" for _ in values)
        task_runs = _rows_by_id(
            _rows(
                connection,
                "task_model_runs",
                where=f"user_id=? AND id IN ({placeholders})",
                parameters=(owner_id, *values),
            )
        )
    fallback_runs = _rows(
        connection,
        "task_model_runs",
        where="user_id=? AND role='memory_extraction'",
        parameters=(owner_id,),
    )
    owner_turn_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM conversation_turns WHERE user_id=?",
            (owner_id,),
        ).fetchall()
    }
    for run in list(task_runs.values()) + fallback_runs:
        if run.get("chat_id") and str(run["chat_id"]) not in all_chats:
            raise BaselineError("Memory task-run chat ownership is inconsistent in the snapshot.")
        if run.get("turn_id") and str(run["turn_id"]) not in owner_turn_ids:
            raise BaselineError("Memory task-run turn ownership is inconsistent in the snapshot.")
    runs_by_turn: dict[str, list[dict]] = {}
    for run in fallback_runs:
        if run.get("turn_id"):
            runs_by_turn.setdefault(str(run["turn_id"]), []).append(run)
    for values in runs_by_turn.values():
        values.sort(key=lambda item: (int(item.get("started_at") or 0), str(item.get("id") or "")), reverse=True)

    resolved = []
    for memory in memories:
        memory_id = str(memory["id"])
        memory_record = records_by_memory.get(memory_id)
        memory_origin = origins_by_memory.get(memory_id)
        source_message_id = memory_origin.get("source_message_id") if memory_origin else memory.get("source_message_id")
        source_turn_id = memory_origin.get("source_turn_id") if memory_origin else memory.get("source_turn_id")
        source_message = messages.get(str(source_message_id or ""))
        source_turn = turns.get(str(source_turn_id or ""))
        source_chat_id = memory_origin.get("source_chat_id") if memory_origin else None
        if not source_chat_id and source_turn:
            source_chat_id = source_turn.get("chat_id")
        elif not source_chat_id and source_message:
            source_chat_id = source_message.get("chat_id")
        source_chat = all_chats.get(str(source_chat_id or ""))
        provenance_resolved = bool(memory_origin and memory_origin.get("provenance_status") == "resolved")
        source_persona_id = (
            memory_origin.get("source_persona_id") if provenance_resolved else (source_chat or {}).get("persona_id")
        )
        source_workspace_id = (
            memory_origin.get("source_workspace_id") if provenance_resolved else (source_chat or {}).get("workspace_id")
        )
        source_persona = persona_rows.get(str(source_persona_id or ""))
        source_workspace = workspace_rows.get(str(source_workspace_id or ""))

        scope = str(memory.get("tier") or "")
        scope_id = memory.get("tier_ref_id")
        if scope == "global":
            scope_resolution = {
                "kind": "global",
                "id": None,
                "name": "Legacy global memory",
                "resolution_status": "resolved",
            }
        elif scope == "workspace":
            value = workspace_rows.get(str(scope_id or ""))
            scope_resolution = {
                "kind": scope,
                "id": scope_id,
                "name": value.get("name") if value else None,
                "resolution_status": "resolved" if value else "referenced_record_unavailable",
            }
        elif scope == "persona":
            value = persona_rows.get(str(scope_id or ""))
            scope_resolution = {
                "kind": scope,
                "id": scope_id,
                "name": value.get("name") if value else None,
                "resolution_status": "resolved" if value else "referenced_record_unavailable",
            }
        elif scope == "chat":
            value = all_chats.get(str(scope_id or ""))
            scope_resolution = {
                "kind": scope,
                "id": scope_id,
                "name": value.get("title") if value else None,
                "resolution_status": "resolved" if value else "referenced_record_unavailable",
            }
        else:
            scope_resolution = {
                "kind": scope,
                "id": scope_id,
                "name": None,
                "resolution_status": "unknown_legacy_scope",
            }

        job_match = candidate_job.get(memory_id)
        job_run = task_runs.get(str(job_match["task_run_id"])) if job_match and job_match.get("task_run_id") else None
        if job_match and job_run:
            extraction = {**job_match, "task_run": job_run}
        elif source_turn_id and runs_by_turn.get(str(source_turn_id)):
            run = runs_by_turn[str(source_turn_id)][0]
            extraction = {
                "match_method": "source_turn_latest_memory_extraction_run",
                "job": job_match.get("job") if job_match else None,
                "task_run_id": run.get("id"),
                "task_run": run,
            }
        else:
            extraction = {
                "match_method": "unavailable",
                "job": None,
                "task_run_id": None,
                "task_run": None,
            }
        if extraction.get("task_run") and extraction["task_run"].get("attempts_json") is not None:
            extraction["task_run"]["attempts"] = _safe_json_object(extraction["task_run"].get("attempts_json"))

        if provenance_resolved:
            unavailable_fields = {
                key: value
                for key, value in UNAVAILABLE_V2_FIELDS.items()
                if key
                in {
                    "qualification_reason",
                    "evidence_spans",
                    "raw_extractor_output",
                    "extractor_decision_trace",
                }
            }
            immutable_source_persona = {
                "available": True,
                "id": source_persona_id,
                "name": source_persona.get("name") if source_persona else None,
                "value_status": ("captured" if source_persona_id else "not_applicable"),
            }
            immutable_source_workspace = {
                "available": True,
                "id": source_workspace_id,
                "name": source_workspace.get("name") if source_workspace else None,
                "value_status": ("captured" if source_workspace_id else "not_applicable"),
            }
        else:
            unavailable_fields = dict(UNAVAILABLE_V2_FIELDS)
            immutable_source_persona = {
                "available": False,
                "id": None,
                "name": None,
                "value_status": "legacy_unresolved",
                "reason": "Legacy Memory v2 did not freeze the persona binding at extraction time.",
            }
            immutable_source_workspace = {
                "available": False,
                "id": None,
                "name": None,
                "value_status": "legacy_unresolved",
                "reason": "Legacy Memory v2 did not freeze the workspace binding at extraction time.",
            }

        resolved.append(
            {
                "memory": memory,
                "memory_record": memory_record,
                "memory_origin": memory_origin,
                "grants": grants_by_memory.get(memory_id, []),
                "grant_events": grant_events_by_memory.get(memory_id, []),
                "events": events_by_memory.get(memory_id, []),
                "scope_resolution": scope_resolution,
                "origin": {
                    "source_message": source_message,
                    "source_turn": source_turn,
                    "source_chat": source_chat,
                    "immutable_source_persona": immutable_source_persona,
                    "immutable_source_workspace": immutable_source_workspace,
                    "evidence": (_safe_json_object(memory_origin.get("evidence_json")) if memory_origin else None),
                    "current_chat_binding_observation": {
                        "chat": source_chat,
                        "persona": (
                            persona_rows.get(str((source_chat or {}).get("persona_id") or "")) if source_chat else None
                        ),
                        "workspace": (
                            workspace_rows.get(str((source_chat or {}).get("workspace_id") or ""))
                            if source_chat
                            else None
                        ),
                        "observed_at_export": True,
                        "caveat": ("These are the chat's current bindings, not immutable extraction-time origin."),
                    },
                    "resolution_status": (
                        str(memory_origin.get("provenance_status"))
                        if memory_origin
                        else "legacy_origin_record_unavailable"
                    ),
                },
                "extraction": extraction,
                "unavailable_current_v2_fields": unavailable_fields,
            }
        )
    resolved.sort(key=lambda item: str(item["memory"]["id"]))
    return resolved, {
        "events": events,
        "workspaces": workspace_rows,
        "personas": persona_rows,
    }


def _identity_reference_asset_verification(
    snapshot: SnapshotDatabase,
    references: list[dict],
) -> dict:
    include_media = bool(snapshot.manifest.get("includeMedia"))
    asset_prefix = "data/identity_references/"
    expected_owner_members = set()
    for reference in references:
        if str(reference.get("review_status") or "") == "deleted":
            continue
        filename = str(reference.get("filename") or "")
        safe_filename = Path(filename).name if filename and Path(filename).name == filename else None
        if safe_filename:
            expected_owner_members.add(f"{asset_prefix}{safe_filename}")
    asset_names = sorted(name for name in expected_owner_members if name in snapshot.archive_names)
    asset_hashes: dict[str, str] = {}
    try:
        with zipfile.ZipFile(snapshot.snapshot_path, "r") as archive:
            asset_infos = [archive.getinfo(name) for name in asset_names]
            if any(info.file_size > MAX_IDENTITY_REFERENCE_ASSET_BYTES for info in asset_infos):
                raise BaselineError("An identity reference asset exceeds the offline safety limit.")
            if sum(info.file_size for info in asset_infos) > MAX_IDENTITY_REFERENCE_TOTAL_BYTES:
                raise BaselineError("Identity reference assets exceed the aggregate offline safety limit.")
            for info in asset_infos:
                digest = hashlib.sha256()
                with archive.open(info, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                asset_hashes[info.filename] = digest.hexdigest()
    except BaselineError:
        raise
    except (EOFError, OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise BaselineError("Identity reference assets could not be safely verified.") from exc
    results = []
    for reference in references:
        deleted = str(reference.get("review_status") or "") == "deleted"
        expected = "" if deleted else str(reference.get("sha256") or "").lower()
        filename = "" if deleted else str(reference.get("filename") or "")
        safe_filename = Path(filename).name if filename and Path(filename).name == filename else None
        expected_member = f"{asset_prefix}{safe_filename}" if safe_filename else None
        if deleted:
            status = "metadata_deleted"
            matches = []
        elif not expected:
            status = "metadata_has_no_sha256"
            matches = []
        elif not expected_member:
            status = "metadata_has_no_safe_filename"
            matches = []
        elif not include_media:
            status = "not_included_by_metadata_only_snapshot"
            matches = []
        elif expected_member not in asset_hashes:
            status = "missing_from_full_snapshot"
            matches = []
        elif asset_hashes[expected_member] != expected:
            status = "hash_mismatch"
            matches = []
        else:
            status = "verified"
            matches = [expected_member]
        results.append(
            {
                "reference_id": reference.get("id"),
                "expected_sha256": expected or None,
                "expected_archive_member": expected_member,
                "status": status,
                "matching_archive_members": matches,
            }
        )
    active_results = [result for result in results if result["status"] != "metadata_deleted"]
    verified_count = sum(result["status"] == "verified" for result in active_results)
    if not include_media:
        verification_status = "not_included"
        note = "This metadata-only snapshot did not include identity reference bytes."
    elif all(result["status"] == "verified" for result in active_results):
        verification_status = "complete"
        note = "Every non-deleted identity reference with metadata was verified against snapshot bytes."
    else:
        verification_status = "incomplete"
        note = (
            "Identity reference verification is incomplete; at least one non-deleted reference is "
            "missing, mismatched, or lacks a stored SHA-256."
        )
    return {
        "include_media": include_media,
        "asset_scope": "selected_owner_references_only",
        "archive_identity_asset_count": len(asset_names),
        "archive_identity_asset_hashes": [
            {"archive_member": name, "sha256": asset_hashes[name]} for name in sorted(asset_hashes)
        ],
        "references": results,
        "verification_status": verification_status,
        "non_deleted_reference_count": len(active_results),
        "verified_reference_count": verified_count,
        "unverified_reference_count": len(active_results) - verified_count,
        "note": note,
    }


def _persona_inventory(
    connection: sqlite3.Connection,
    owner_id: str,
    snapshot: SnapshotDatabase | None = None,
) -> dict:
    available = _table_names(connection)
    workspaces = _rows(connection, "workspaces", where="user_id=?", parameters=(owner_id,))
    workspace_ids = {str(row["id"]) for row in workspaces}
    all_links = _rows(connection, "persona_workspace_links")
    persona_ids = {str(row["persona_id"]) for row in all_links if str(row.get("workspace_id") or "") in workspace_ids}
    all_personas = _rows(connection, "personas")
    persona_ids.update(str(row["id"]) for row in all_personas if str(row.get("workspace_id") or "") in workspace_ids)
    persona_by_id = {str(row["id"]): row for row in all_personas}
    for persona_id in persona_ids:
        persona = persona_by_id.get(persona_id)
        if not persona or str(persona.get("workspace_id") or "") not in workspace_ids:
            raise BaselineError("Persona workspace ownership is inconsistent in the snapshot.")
    if any(
        str(link.get("workspace_id") or "") not in workspace_ids
        for link in all_links
        if str(link.get("persona_id") or "") in persona_ids
    ):
        raise BaselineError("Persona workspace membership crosses owner boundaries.")
    personas = [row for row in all_personas if str(row.get("id") or "") in persona_ids]
    links = [row for row in all_links if str(row.get("persona_id") or "") in persona_ids]
    visual_identities = (
        _rows(connection, "persona_visual_identities", where="user_id=?", parameters=(owner_id,))
        if "persona_visual_identities" in available
        else []
    )
    references = (
        _rows(connection, "persona_identity_references", where="user_id=?", parameters=(owner_id,))
        if "persona_identity_references" in available
        else []
    )
    identity_ids = {str(row.get("id") or "") for row in visual_identities}
    media_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM media_files WHERE user_id=?",
            (owner_id,),
        ).fetchall()
    }
    for reference in references:
        filename = str(reference.get("filename") or "")
        local_path = str(reference.get("local_path") or "")
        if str(reference.get("identity_id") or "") not in identity_ids:
            raise BaselineError("Persona identity reference ownership is inconsistent in the snapshot.")
        source_media_id = reference.get("source_media_id")
        if source_media_id is not None and str(source_media_id) not in media_ids:
            raise BaselineError("Persona identity reference ownership is inconsistent in the snapshot.")
        if str(reference.get("review_status") or "") == "deleted":
            if filename or local_path:
                raise BaselineError("Persona identity asset ownership is inconsistent in the snapshot.")
            continue
        if not filename or "/" in filename or "\\" in filename or not filename.startswith(f"{owner_id}_"):
            raise BaselineError("Persona identity asset ownership is inconsistent in the snapshot.")
        if local_path and Path(local_path).name != filename:
            raise BaselineError("Persona identity asset ownership is inconsistent in the snapshot.")
    if any(str(row.get("persona_id") or "") not in persona_ids for row in visual_identities + references):
        raise BaselineError("Persona identity metadata crosses owner boundaries.")
    raw_tables = {
        "workspaces": workspaces,
        "personas": personas,
        "persona_workspace_links": links,
        "persona_visual_identities": visual_identities,
        "persona_identity_references": references,
    }
    schemas = {
        table: {
            "columns": _table_columns(connection, table),
            "schema_sql": _schema_sql(connection, table),
        }
        for table in RAW_PERSONA_TABLES
        if table in available
    }
    payload = {"schemas": schemas, "rows": raw_tables}
    result = {
        **payload,
        "sha256": _canonical_sha256(payload),
        "persona_count": len(personas),
    }
    if snapshot is not None:
        result["identity_reference_assets"] = _identity_reference_asset_verification(
            snapshot,
            references,
        )
    return result


def _memory_fingerprints(memories: list[dict], events: list[dict]) -> dict[str, str]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(str(event.get("memory_id") or ""), []).append(event)
    return {
        str(memory["id"]): _canonical_sha256(
            {
                "memory": memory,
                "events": sorted(grouped.get(str(memory["id"]), []), key=_canonical_bytes),
            }
        )
        for memory in memories
    }


def _normalized_text(value) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _definition_tokens(value) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]{3,}", _normalized_text(value), flags=re.UNICODE)
        if token not in DEFINITION_STOP_WORDS
    }


def _trait_leaf_values(value) -> list[str]:
    if isinstance(value, dict):
        result = []
        for child in value.values():
            result.extend(_trait_leaf_values(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_trait_leaf_values(child))
        return result
    if isinstance(value, str) and _normalized_text(value):
        return [value]
    return []


def _persona_definition_material(persona_inventory: dict) -> dict:
    definitions = []
    trait_values = []
    names = []
    rows = (persona_inventory.get("rows") or {}).get("personas") or []
    for persona in rows:
        name = _normalized_text(persona.get("name"))
        if name:
            names.append(name)
        generated = _normalized_text(persona_instruction_block(persona))
        if generated:
            definitions.append(generated)
            definitions.extend(
                normalized
                for normalized in (_normalized_text(line) for line in persona_instruction_block(persona).splitlines())
                if normalized
            )
        for field in ("system_prompt", "personality_details"):
            value = _normalized_text(persona.get(field))
            if value:
                definitions.append(value)
        traits = _safe_json_object(persona.get("traits_json"))
        for leaf in _trait_leaf_values(traits):
            normalized = _normalized_text(leaf)
            if normalized:
                trait_values.append(normalized)
                definitions.append(normalized)
    return {
        "definitions": sorted(set(definitions)),
        "trait_values": sorted(set(trait_values)),
        "names": sorted(set(names)),
    }


def _instruction_flags(memory: dict, persona_inventory: dict) -> list[str]:
    """Return conservative content-based quarantine reasons.

    Scope and a common persona name alone are intentionally insufficient. The
    purpose is to preserve possible definition material, not every memory that
    happens to be persona-scoped.
    """

    flags = []
    content = _normalized_text(memory.get("content"))
    if not content:
        return flags
    material = _persona_definition_material(persona_inventory)
    content_tokens = _definition_tokens(content)
    persona_directive = bool(DIRECTIVE_PATTERN.search(content))
    instruction_like = persona_directive or bool(IMPERATIVE_INSTRUCTION_PATTERN.search(content))

    for definition in material["definitions"]:
        if content == definition:
            flags.append("definition_exact")
            break
    if "definition_exact" not in flags:
        for definition in material["definitions"]:
            shorter = min(len(content), len(definition))
            if shorter >= 16 and (content in definition or definition in content):
                flags.append("definition_substring")
                break
    if not {"definition_exact", "definition_substring"}.intersection(flags):
        for definition in material["definitions"]:
            definition_tokens = _definition_tokens(definition)
            shared = content_tokens.intersection(definition_tokens)
            if len(shared) >= 3 and len(shared) / max(1, min(len(content_tokens), len(definition_tokens))) >= 0.6:
                flags.append("definition_token_overlap")
                break

    for trait in material["trait_values"]:
        trait_tokens = _definition_tokens(trait)
        if (
            content == trait
            or (len(trait) >= 4 and re.search(rf"(?<!\w){re.escape(trait)}(?!\w)", content))
            or (len(trait_tokens) >= 2 and trait_tokens.issubset(content_tokens))
        ):
            flags.append("trait_value_overlap")
            break

    if persona_directive:
        flags.append("persona_directive")
    if DIRECTIVE_VERB_PATTERN.search(content) and any(name in content for name in material["names"] if len(name) >= 2):
        flags.append("persona_name_plus_directive")
    if PERSONA_DESCRIPTION_PATTERN.search(content) and any(
        name in content for name in material["names"] if len(name) >= 2
    ):
        flags.append("persona_name_plus_description")
    if instruction_like:
        flags.append("instruction_like")
    return sorted(set(flags))


def _memory_components(
    memories: list[dict],
    events: list[dict],
    origins: list[dict] = (),
) -> list[list[str]]:
    memory_ids = {str(row["id"]) for row in memories}
    graph = {memory_id: set() for memory_id in memory_ids}

    def connect(left, right):
        left = str(left or "")
        right = str(right or "")
        if left in graph and right in graph and left != right:
            graph[left].add(right)
            graph[right].add(left)

    for memory in memories:
        connect(memory.get("id"), memory.get("supersedes_id"))
    for event in events:
        connect(event.get("memory_id"), event.get("related_memory_id"))
    for origin in origins:
        connect(
            origin.get("memory_id"),
            origin.get("revision_of_memory_id"),
        )

    components = []
    remaining = set(memory_ids)
    while remaining:
        start = min(remaining)
        stack = [start]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(graph[current] - component, reverse=True))
        remaining -= component
        components.append(sorted(component))
    components.sort(key=lambda item: item[0] if item else "")
    return components


def _dependency_impact(
    connection: sqlite3.Connection,
    owner_id: str,
    delete_ids: list[str],
) -> dict:
    if not delete_ids:
        return {
            "target_memory_count": 0,
            "cascading_event_count": 0,
            "fts_row_count": 0,
            "retained_memory_set_null_ids": [],
            "retained_event_set_null_ids": [],
            "safe_component_closure": True,
        }
    placeholders = ",".join("?" for _ in delete_ids)
    parameters = tuple(delete_ids)
    target_count = connection.execute(
        f"SELECT COUNT(*) FROM memories WHERE user_id=? AND id IN ({placeholders})",
        (owner_id, *parameters),
    ).fetchone()[0]
    event_count = connection.execute(
        f"SELECT COUNT(*) FROM memory_events WHERE memory_id IN ({placeholders})",
        parameters,
    ).fetchone()[0]
    fts_count = connection.execute(
        f"SELECT COUNT(*) FROM memory_fts WHERE user_id=? AND memory_id IN ({placeholders})",
        (owner_id, *parameters),
    ).fetchone()[0]
    retained_memories = [
        str(row[0])
        for row in connection.execute(
            f"SELECT id FROM memories WHERE id NOT IN ({placeholders}) "
            f"AND supersedes_id IN ({placeholders}) ORDER BY id",
            (*parameters, *parameters),
        ).fetchall()
    ]
    retained_events = [
        str(row[0])
        for row in connection.execute(
            f"SELECT id FROM memory_events WHERE memory_id NOT IN ({placeholders}) "
            f"AND related_memory_id IN ({placeholders}) ORDER BY id",
            (*parameters, *parameters),
        ).fetchall()
    ]
    return {
        "target_memory_count": int(target_count),
        "cascading_event_count": int(event_count),
        "fts_row_count": int(fts_count),
        "retained_memory_set_null_ids": retained_memories,
        "retained_event_set_null_ids": retained_events,
        "safe_component_closure": not retained_memories and not retained_events,
    }


def _build_reset_plan(
    connection: sqlite3.Connection,
    baseline_sha256: str,
    baseline: dict,
    memories: list[dict],
    events: list[dict],
) -> dict:
    all_ids = {str(row["id"]) for row in memories}
    records_by_memory = {str(row["memory_id"]): row for row in baseline["memory_v3"]["memory_records"]}
    legacy_reset_ids = {
        memory_id
        for memory_id, record in records_by_memory.items()
        if record.get("lineage") == "legacy_migrated" and record.get("access_state") == "legacy_quarantined"
    }
    native_v3_ids = all_ids - legacy_reset_ids
    flags = {
        str(row["id"]): (
            _instruction_flags(row, baseline["persona_inventory"]) if str(row["id"]) in legacy_reset_ids else []
        )
        for row in memories
    }
    components = _memory_components(
        memories,
        events,
        baseline["memory_v3"]["memory_origins"],
    )
    quarantined = set()
    component_records = []
    for component in components:
        eligible_ids = sorted(set(component).intersection(legacy_reset_ids))
        retained_v3_ids = sorted(set(component).intersection(native_v3_ids))
        reasons = {reason for memory_id in eligible_ids for reason in flags[memory_id]}
        if eligible_ids and retained_v3_ids:
            reasons.add("linked_to_native_v3_memory")
        reason_codes = sorted(reasons)
        quarantined_ids = eligible_ids if reason_codes else []
        quarantined.update(quarantined_ids)
        if "linked_to_native_v3_memory" in reasons:
            closure_reason = (
                "Legacy members are retained because the same revision/event component contains native v3 memory."
            )
        elif reason_codes:
            closure_reason = (
                "Eligible legacy members are quarantined because at least one may contain persona instructions."
            )
        else:
            closure_reason = None
        component_records.append(
            {
                "component_id": _canonical_sha256(component)[:24],
                "memory_ids": component,
                "legacy_reset_eligible_ids": eligible_ids,
                "native_v3_keep_ids": retained_v3_ids,
                "quarantined_memory_ids": quarantined_ids,
                "quarantined": bool(quarantined_ids),
                "reason_codes": reason_codes,
                "closure_reason": closure_reason,
            }
        )
    delete_ids = sorted(legacy_reset_ids - quarantined)
    quarantine_ids = sorted(quarantined)
    keep_ids = sorted(native_v3_ids)
    fingerprints = _memory_fingerprints(memories, events)
    dependency_impact = _dependency_impact(
        connection,
        str(baseline["owner"]["id"]),
        delete_ids,
    )
    plan = {
        "format": "nice-assistant-disposable-memory-reset-plan",
        "format_version": EXPORT_FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "owner_id": baseline["owner"]["id"],
        "source": {
            "snapshot_sha256": baseline["source"]["snapshot_sha256"],
            "database_sha256": baseline["source"]["database_sha256"],
            "schema_revision": baseline["source"]["schema_revision"],
            "baseline_sha256": baseline_sha256,
        },
        "dispositions": {
            "delete_ids": delete_ids,
            "quarantine_ids": quarantine_ids,
            "keep_ids": keep_ids,
            "undecided_ids": [],
        },
        "memory_fingerprints": fingerprints,
        "memory_fts_sha256": _canonical_sha256(_rows(connection, "memory_fts")),
        "component_closure": component_records,
        "dependency_impact": dependency_impact,
        "persona_inventory_sha256": baseline["persona_inventory"]["sha256"],
        "protected_non_memory_sha256": baseline["protected_non_memory"]["sha256"],
        "foreign_key_inventory_sha256": baseline["foreign_key_inventory"]["sha256"],
        "target_set_sha256": _canonical_sha256(delete_ids),
        "owner_confirmation_status": "not_requested",
        "live_execution_supported": False,
        "disposable_drill_only": True,
    }
    if not dependency_impact["safe_component_closure"]:
        raise BaselineError("Generated reset plan would alter retained memory history.")
    plan["plan_sha256"] = _canonical_sha256(plan)
    return plan


def _build_baseline(snapshot: SnapshotDatabase, owner_id: str | None) -> tuple[dict, dict]:
    connection = sqlite3.connect(snapshot.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        _validate_database(connection)
        owner = _owner_record(connection, owner_id)
        memories = _rows(connection, "memories", where="user_id=?", parameters=(owner["id"],))
        memory_ids = sorted(str(row["id"]) for row in memories)
        placeholders = ",".join("?" for _ in memory_ids)
        memory_v3 = {}
        for table in ("memory_records", "memory_origins", "memory_grants", "memory_grant_events"):
            memory_v3[table] = (
                _rows(
                    connection,
                    table,
                    where=f"memory_id IN ({placeholders})",
                    parameters=tuple(memory_ids),
                )
                if memory_ids
                else []
            )
        expected_memory_ids = set(memory_ids)
        for table in ("memory_records", "memory_origins"):
            if {str(row.get("memory_id") or "") for row in memory_v3[table]} != expected_memory_ids:
                raise BaselineError(f"Memory v3 {table} coverage is inconsistent in the snapshot.")
        human_rows = connection.execute(
            "SELECT id FROM human_principals WHERE user_id=?",
            (owner["id"],),
        ).fetchall()
        if len(human_rows) != 1:
            raise BaselineError("Memory principal ownership is inconsistent in the snapshot.")
        human_id = str(human_rows[0][0])
        if any(str(row.get("human_id") or "") != human_id for rows in memory_v3.values() for row in rows):
            raise BaselineError("Memory v3 row ownership is inconsistent in the snapshot.")
        if any(
            str(grant.get("granted_by_human_id") or "") != human_id
            or (grant.get("revoked_by_human_id") is not None and str(grant["revoked_by_human_id"]) != human_id)
            for grant in memory_v3["memory_grants"]
        ):
            raise BaselineError("Memory grant actor ownership is inconsistent in the snapshot.")
        reset_eligible_ids = {
            str(record["memory_id"])
            for record in memory_v3["memory_records"]
            if record.get("lineage") == "legacy_migrated" and record.get("access_state") == "legacy_quarantined"
        }
        access_ledger_ids = {
            str(row["memory_id"]) for table in ("memory_grants", "memory_grant_events") for row in memory_v3[table]
        }
        if reset_eligible_ids.intersection(access_ledger_ids):
            raise BaselineError("A legacy reset-eligible memory has an access ledger in the snapshot.")
        _validate_v3_reference_ownership(
            connection,
            owner["id"],
            memories,
            memory_v3,
        )
        resolved, support = _source_and_task_resolution(
            connection,
            owner["id"],
            memories,
            memory_v3,
        )
        memory_events = support["events"]
        memory_schema = {
            table: {
                "columns": _table_columns(connection, table),
                "schema_sql": _schema_sql(connection, table),
            }
            for table in sorted(MEMORY_TABLES)
        }
        baseline = {
            "format": EXPORT_FORMAT,
            "format_version": EXPORT_FORMAT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "snapshot_sha256": snapshot.snapshot_sha256,
                "snapshot_size": snapshot.snapshot_size,
                "database_sha256": snapshot.database_sha256,
                "schema_revision": snapshot.schema_revision,
                "snapshot_manifest": snapshot.manifest,
            },
            "owner": owner,
            "memory_schema": memory_schema,
            "memories": memories,
            "memory_events": memory_events,
            "memory_v3": memory_v3,
            "resolved_memories": resolved,
            "persona_inventory": _persona_inventory(connection, owner["id"], snapshot),
            "protected_non_memory": _protected_non_memory_hashes(connection),
            "foreign_key_inventory": _foreign_key_inventory(connection),
            "unavailable_current_v2_fields": dict(UNAVAILABLE_V2_FIELDS),
        }
        baseline["counts"] = {
            "memories": len(memories),
            "memory_events": len(memory_events),
            "memory_records": len(memory_v3["memory_records"]),
            "memory_origins": len(memory_v3["memory_origins"]),
            "memory_grants": len(memory_v3["memory_grants"]),
            "memory_grant_events": len(memory_v3["memory_grant_events"]),
            "by_status": _count_values(memories, "status"),
            "by_scope": _count_values(memories, "tier"),
            "by_source_type": _count_values(memories, "source_type"),
        }
        baseline_sha256 = _canonical_sha256(baseline)
        plan = _build_reset_plan(
            connection,
            baseline_sha256,
            baseline,
            memories,
            memory_events,
        )
        return baseline, plan
    finally:
        connection.close()


def _count_values(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unavailable")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _outside_repository(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
        return False
    except ValueError:
        return True


def _private_output_directory(path) -> Path:
    output = Path(path).expanduser().resolve()
    if not _outside_repository(output):
        raise BaselineError("Private baseline artifacts cannot be written anywhere inside the repository.")
    if output.exists() and (not output.is_dir() or output.is_symlink()):
        raise BaselineError("Private output path must be a real directory, not a file or symbolic link.")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(output, 0o700)
    except OSError:
        pass
    return output


def _write_unique_private(path: Path, content: bytes) -> None:
    if not content:
        raise BaselineError("Refusing to write an empty private artifact.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except FileExistsError as exc:
        raise BaselineError("Private artifact name collision; retry the export.") from exc
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise BaselineError("Private artifact could not be written.") from exc


def _permission_verification(directory: Path, files: tuple[Path, ...]) -> dict:
    if os.name == "nt":
        return {
            "owner_only_verified": False,
            "mechanism": "windows_acl_not_verified",
            "warning": (
                "chmod does not prove owner-only access on Windows. The artifacts are outside the repository, "
                "but their inherited Windows ACL must be reviewed before treating them as owner-only."
            ),
        }
    labeled_paths = (
        ("output_directory", directory),
        ("json_artifact", files[0]),
        ("text_artifact", files[1]),
    )
    modes = {label: stat.S_IMODE(path.stat().st_mode) for label, path in labeled_paths}
    verified = all((mode & 0o077) == 0 for mode in modes.values())
    return {
        "owner_only_verified": verified,
        "mechanism": "posix_mode_bits",
        "modes": {name: oct(mode) for name, mode in modes.items()},
        "warning": None if verified else "One or more private artifact paths permit group or other access.",
    }


def _report_text(value) -> str:
    """Render untrusted display text as one escaped, unambiguous line."""

    return json.dumps(str(value), ensure_ascii=True)


def _readable_summary(document: dict) -> str:
    baseline = document["baseline"]
    plan = document["reset_plan"]
    dispositions = plan["dispositions"]
    disposition_by_id = {
        memory_id: label
        for label, values in (
            ("DELETE IN DISPOSABLE DRILL", dispositions["delete_ids"]),
            ("QUARANTINE", dispositions["quarantine_ids"]),
            ("KEEP", dispositions["keep_ids"]),
            ("UNDECIDED", dispositions["undecided_ids"]),
        )
        for memory_id in values
    }
    component_by_id = {}
    for component in plan["component_closure"]:
        for memory_id in component["memory_ids"]:
            component_by_id[memory_id] = component
    lines = [
        "Nice Assistant private memory baseline",
        "",
        "This artifact contains private memory and persona information.",
        "It is not a live reset command and must never be committed to the repository.",
        "On Windows, chmod is not proof of an owner-only ACL; review the inherited ACL before retention.",
        "",
        f"Owner ID: {baseline['owner']['id']}",
        f"Schema revision: {baseline['source']['schema_revision']}",
        f"Snapshot SHA-256: {baseline['source']['snapshot_sha256']}",
        f"Baseline SHA-256: {document['baseline_sha256']}",
        f"Reset plan SHA-256: {plan['plan_sha256']}",
        f"Memory count: {baseline['counts']['memories']}",
        f"Quarantine count: {len(dispositions['quarantine_ids'])}",
        f"Disposable-drill delete count: {len(dispositions['delete_ids'])}",
        "",
    ]
    for item in baseline["resolved_memories"]:
        memory = item["memory"]
        record = item.get("memory_record") or {}
        source_persona = item["origin"]["immutable_source_persona"]
        source_workspace = item["origin"]["immutable_source_workspace"]
        memory_id = str(memory["id"])
        component = component_by_id.get(memory_id) or {}
        if source_persona.get("available"):
            source_persona_text = str(source_persona.get("id") or "none captured")
            if source_persona.get("name"):
                source_persona_text += f" ({_report_text(source_persona['name'])})"
        else:
            source_persona_text = "unavailable for legacy unresolved provenance"
        if source_workspace.get("available"):
            source_workspace_text = str(source_workspace.get("id") or "none captured")
            if source_workspace.get("name"):
                source_workspace_text += f" ({_report_text(source_workspace['name'])})"
        else:
            source_workspace_text = "unavailable for legacy unresolved provenance"
        grant_summaries = []
        for grant in item.get("grants") or []:
            target_id = grant.get("persona_id") or grant.get("workspace_id")
            state = "revoked" if grant.get("revoked_at") is not None else "active"
            details = [
                state,
                f"source={grant.get('grant_source')}",
                f"granted_at={grant.get('granted_at')}",
                f"granted_by={grant.get('granted_by_human_id')}",
            ]
            if grant.get("revoked_at") is not None:
                details.extend(
                    [
                        f"revoked_at={grant.get('revoked_at')}",
                        f"revoked_by={grant.get('revoked_by_human_id')}",
                    ]
                )
            grant_summaries.append(f"{grant.get('grant_type')}:{target_id} ({', '.join(details)})")
        grant_event_summaries = [
            (
                f"{event.get('action')} {event.get('grant_type')}:"
                f"{event.get('target_id')} at {event.get('created_at')} "
                f"(grant={event.get('grant_id')})"
            )
            for event in item.get("grant_events") or []
        ]
        unavailable_fields = sorted((item.get("unavailable_current_v2_fields") or {}).keys())
        lines.extend(
            [
                f"[{disposition_by_id.get(memory_id, 'UNCLASSIFIED')}] {memory_id}",
                f"Status: {memory.get('status')}",
                f"Access state: {record.get('access_state') or 'unavailable'}",
                f"Memory type: {record.get('memory_type') or 'unavailable'}",
                f"Validity: {record.get('validity_status') or 'unavailable'}",
                f"Valid until: {record.get('valid_until') or 'not applicable'}",
                f"Stateful status: {record.get('stateful_status') or 'not applicable'}",
                f"Last confirmed: {record.get('last_confirmed_at') or 'unavailable'}",
                f"Legacy scope: {memory.get('tier')} / {memory.get('tier_ref_id')}",
                "Resolved legacy scope: "
                + (
                    _report_text(item["scope_resolution"]["name"])
                    if item["scope_resolution"].get("name")
                    else "unavailable"
                ),
                "Access grants: " + (", ".join(grant_summaries) or "none"),
                "Grant events: " + (", ".join(grant_event_summaries) or "none"),
                f"Source type: {memory.get('source_type')}",
                f"Confidence: {memory.get('confidence')}",
                f"Content: {_report_text(memory.get('content') or '')}",
                "Quarantine flags: " + (", ".join(component.get("reason_codes") or []) or "none"),
                f"Source chat: {(item['origin'].get('source_chat') or {}).get('id') or 'unavailable'}",
                f"Immutable source persona: {source_persona_text}",
                f"Immutable source workspace: {source_workspace_text}",
                "Current chat persona observation (not extraction-time origin): "
                + (
                    _report_text(
                        (item["origin"].get("current_chat_binding_observation", {}).get("persona") or {}).get("name")
                    )
                    if (item["origin"].get("current_chat_binding_observation", {}).get("persona") or {}).get("name")
                    else "unavailable"
                ),
                "Current chat workspace observation (not extraction-time origin): "
                + (
                    _report_text(
                        (item["origin"].get("current_chat_binding_observation", {}).get("workspace") or {}).get("name")
                    )
                    if (item["origin"].get("current_chat_binding_observation", {}).get("workspace") or {}).get("name")
                    else "unavailable"
                ),
                f"Extraction match: {item['extraction'].get('match_method')}",
                f"Event count: {len(item.get('events') or [])}",
                "Fields unavailable for this record: " + (", ".join(unavailable_fields) or "none"),
                "",
            ]
        )
    lines.extend(
        [
            "Fields unavailable for legacy Memory v2 rows",
            "",
            *[f"- {key}: {value}" for key, value in sorted(UNAVAILABLE_V2_FIELDS.items())],
            "",
        ]
    )
    return "\n".join(lines)


def export_memory_baseline(
    snapshot,
    *,
    output_dir,
    owner_id: str | None = None,
) -> BaselineExportResult:
    """Export a private baseline and disposable-only reset plan from a snapshot."""

    output = _private_output_directory(output_dir)
    with extracted_snapshot_database(snapshot) as extracted:
        baseline, reset_plan = _build_baseline(extracted, owner_id)
        baseline_sha256 = _canonical_sha256(baseline)
        if reset_plan["source"]["baseline_sha256"] != baseline_sha256:
            raise BaselineError("Internal baseline hash mismatch.")
        document = {
            "format": EXPORT_FORMAT,
            "format_version": EXPORT_FORMAT_VERSION,
            "baseline_sha256": baseline_sha256,
            "baseline": baseline,
            "reset_plan": reset_plan,
            "artifact_security_notice": (
                "Artifacts are outside the repository. On Windows, chmod does not prove owner-only ACLs; "
                "the CLI reports that ACL verification remains outstanding."
            ),
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        token = secrets.token_hex(4)
        base_name = f"nice-assistant-private-memory-baseline-{stamp}-{token}"
        json_path = output / f"{base_name}.json"
        text_path = output / f"{base_name}.txt"
        json_content = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        text_content = _readable_summary(document).encode("utf-8")
        if len(json_content) > MAX_BASELINE_BYTES or len(text_content) > MAX_BASELINE_BYTES:
            raise BaselineError("Private baseline artifact exceeds the offline safety limit.")
        dispositions = reset_plan["dispositions"]
        memory_count = len(baseline["memories"])
        owner_id_value = str(baseline["owner"]["id"])

    # The context manager verifies the source ZIP is unchanged before any
    # private artifact is published.
    _write_unique_private(json_path, json_content)
    try:
        _write_unique_private(text_path, text_content)
    except Exception:
        json_path.unlink(missing_ok=True)
        raise
    return BaselineExportResult(
        json_path=json_path,
        text_path=text_path,
        baseline_sha256=baseline_sha256,
        reset_plan_sha256=reset_plan["plan_sha256"],
        memory_count=memory_count,
        quarantine_count=len(dispositions["quarantine_ids"]),
        delete_count=len(dispositions["delete_ids"]),
        owner_id=owner_id_value,
        permission_verification=_permission_verification(
            output,
            (json_path, text_path),
        ),
    )


def _load_baseline(path) -> dict:
    baseline_path = Path(path).expanduser().resolve()
    if not baseline_path.is_file():
        raise BaselineError("Baseline JSON does not exist.")
    if not _outside_repository(baseline_path):
        raise BaselineError("Baseline JSON must be private and outside the repository.")
    if baseline_path.stat().st_size <= 0 or baseline_path.stat().st_size > MAX_BASELINE_BYTES:
        raise BaselineError("Baseline JSON size is invalid.")
    try:
        document = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (MemoryError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError("Baseline JSON is invalid.") from exc
    if not isinstance(document, dict):
        raise BaselineError("Baseline JSON is invalid.")
    try:
        format_version = int(document.get("format_version"))
    except (TypeError, ValueError) as exc:
        raise BaselineError("Baseline JSON has an unsupported format.") from exc
    if (
        document.get("format") != EXPORT_FORMAT
        or format_version != EXPORT_FORMAT_VERSION
        or not isinstance(document.get("baseline"), dict)
        or not isinstance(document.get("reset_plan"), dict)
    ):
        raise BaselineError("Baseline JSON has an unsupported format.")
    calculated_baseline = _canonical_sha256(document["baseline"])
    if document.get("baseline_sha256") != calculated_baseline:
        raise BaselineError("Baseline JSON failed its baseline hash check.")
    plan = dict(document["reset_plan"])
    supplied_plan_hash = plan.pop("plan_sha256", None)
    if supplied_plan_hash != _canonical_sha256(plan):
        raise BaselineError("Baseline JSON failed its reset-plan hash check.")
    if document["reset_plan"].get("live_execution_supported") is not False:
        raise BaselineError("Baseline reset plan is not marked disposable-only.")
    if document["reset_plan"].get("disposable_drill_only") is not True:
        raise BaselineError("Baseline reset plan is not marked disposable-only.")
    return document


def _validate_dispositions(plan: dict, all_memory_ids: set[str]) -> tuple[list[str], set[str]]:
    dispositions = plan.get("dispositions")
    if not isinstance(dispositions, dict):
        raise BaselineError("Reset plan dispositions are invalid.")
    keys = ("delete_ids", "quarantine_ids", "keep_ids", "undecided_ids")
    values: dict[str, list[str]] = {}
    seen = set()
    for key in keys:
        raw = dispositions.get(key)
        if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
            raise BaselineError("Reset plan dispositions are invalid.")
        if raw != sorted(set(raw)):
            raise BaselineError("Reset plan disposition IDs must be unique and sorted.")
        overlap = seen.intersection(raw)
        if overlap:
            raise BaselineError("Reset plan disposition sets overlap.")
        seen.update(raw)
        values[key] = raw
    if seen != all_memory_ids:
        raise BaselineError("Reset plan does not classify exactly the exported memory IDs.")
    if values["undecided_ids"]:
        raise BaselineError("Reset plan still has undecided memory IDs.")
    return values["delete_ids"], seen - set(values["delete_ids"])


def _all_database_memory_state(connection: sqlite3.Connection) -> tuple[list[dict], list[dict], dict]:
    memories = _rows(connection, "memories")
    events = _rows(connection, "memory_events")
    return memories, events, _memory_fingerprints(memories, events)


def _memory_owned_dependency_state(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    return {table: _rows(connection, table) for table in sorted(MEMORY_TABLES - {"memories", "memory_events"})}


def _baseline_without_generation_time(value: dict) -> dict:
    comparable = dict(value)
    comparable.pop("generated_at", None)
    return comparable


def _verify_deterministic_reset_plan(
    snapshot: SnapshotDatabase,
    owner_id: str,
    supplied_baseline: dict,
    supplied_plan: dict,
) -> None:
    """Re-derive the private review baseline and every safety-relevant plan field."""

    expected_baseline, expected_plan = _build_baseline(snapshot, owner_id)
    if _baseline_without_generation_time(supplied_baseline) != _baseline_without_generation_time(expected_baseline):
        raise BaselineError("Private review baseline differs from the bound snapshot.")
    security_fields = (
        "format",
        "format_version",
        "owner_id",
        "dispositions",
        "memory_fingerprints",
        "memory_fts_sha256",
        "component_closure",
        "dependency_impact",
        "persona_inventory_sha256",
        "protected_non_memory_sha256",
        "foreign_key_inventory_sha256",
        "target_set_sha256",
        "owner_confirmation_status",
        "live_execution_supported",
        "disposable_drill_only",
    )
    for field in security_fields:
        if supplied_plan.get(field) != expected_plan.get(field):
            raise BaselineError("Reset plan differs from the deterministic snapshot-derived safety plan.")


def drill_memory_reset(snapshot, baseline_json) -> dict:
    """Exercise the frozen exact-ID reset only on an internally extracted copy."""

    document = _load_baseline(baseline_json)
    baseline = document["baseline"]
    plan = document["reset_plan"]
    if plan.get("source", {}).get("baseline_sha256") != document["baseline_sha256"]:
        raise BaselineError("Reset plan is not bound to this baseline.")
    with extracted_snapshot_database(snapshot) as extracted:
        if extracted.snapshot_sha256 != plan.get("source", {}).get("snapshot_sha256"):
            raise BaselineError("Snapshot does not match the frozen reset plan.")
        if extracted.database_sha256 != plan.get("source", {}).get("database_sha256"):
            raise BaselineError("Snapshot database does not match the frozen reset plan.")
        if extracted.schema_revision != plan.get("source", {}).get("schema_revision"):
            raise BaselineError("Snapshot schema does not match the frozen reset plan.")

        connection = sqlite3.connect(extracted.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")
        try:
            _validate_database(connection)
            owner_id = str(plan.get("owner_id") or "")
            baseline_owner_id = str((baseline.get("owner") or {}).get("id") or "")
            if not owner_id or owner_id != baseline_owner_id:
                raise BaselineError("Reset plan owner does not match the private review baseline.")
            _owner_record(connection, owner_id)
            _verify_deterministic_reset_plan(extracted, owner_id, baseline, plan)
            owner_memories = _rows(connection, "memories", where="user_id=?", parameters=(owner_id,))
            owner_memory_ids = {str(row["id"]) for row in owner_memories}
            if owner_memory_ids:
                placeholders = ",".join("?" for _ in owner_memory_ids)
                owner_events = _rows(
                    connection,
                    "memory_events",
                    where=f"memory_id IN ({placeholders})",
                    parameters=tuple(sorted(owner_memory_ids)),
                )
            else:
                owner_events = []
            delete_ids, _retained_owner_ids = _validate_dispositions(plan, owner_memory_ids)
            current_fingerprints = _memory_fingerprints(owner_memories, owner_events)
            if current_fingerprints != plan.get("memory_fingerprints"):
                raise BaselineError("Memory rows or history have changed since the frozen baseline.")
            if _canonical_sha256(delete_ids) != plan.get("target_set_sha256"):
                raise BaselineError("Reset target set hash does not match.")

            persona_inventory = _persona_inventory(connection, owner_id)
            if persona_inventory["sha256"] != plan.get("persona_inventory_sha256"):
                raise BaselineError("Persona definitions do not match the frozen baseline.")
            protected_before = _protected_non_memory_hashes(connection)
            if protected_before["sha256"] != plan.get("protected_non_memory_sha256"):
                raise BaselineError("Protected non-memory state does not match the frozen baseline.")
            foreign_keys = _foreign_key_inventory(connection)
            if foreign_keys["sha256"] != plan.get("foreign_key_inventory_sha256"):
                raise BaselineError("Foreign-key structure does not match the frozen baseline.")
            impact = _dependency_impact(connection, owner_id, delete_ids)
            if impact != plan.get("dependency_impact") or not impact["safe_component_closure"]:
                raise BaselineError("Reset dependency impact does not match the frozen plan.")

            all_memories_before, all_events_before, all_fingerprints_before = _all_database_memory_state(connection)
            owned_dependencies_before = _memory_owned_dependency_state(connection)
            target_set = set(delete_ids)
            all_fts_before = _rows(connection, "memory_fts")
            if _canonical_sha256(all_fts_before) != plan.get("memory_fts_sha256"):
                raise BaselineError("Memory search-index rows do not match the frozen baseline.")
            retained_fts_before = [row for row in all_fts_before if str(row.get("memory_id") or "") not in target_set]
            retained_fingerprints_before = {
                memory_id: fingerprint
                for memory_id, fingerprint in all_fingerprints_before.items()
                if memory_id not in target_set
            }
            retained_dependencies_before = {
                table: [row for row in rows if str(row.get("memory_id") or "") not in target_set]
                for table, rows in owned_dependencies_before.items()
            }
            target_event_ids = {
                str(row["id"]) for row in all_events_before if str(row.get("memory_id") or "") in target_set
            }

            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("CREATE TEMP TABLE reset_target_ids(id TEXT PRIMARY KEY NOT NULL)")
                connection.executemany(
                    "INSERT INTO reset_target_ids(id) VALUES(?)",
                    [(memory_id,) for memory_id in delete_ids],
                )
                before_changes = connection.total_changes
                connection.execute(
                    "DELETE FROM memories WHERE user_id=? AND id IN (SELECT id FROM reset_target_ids)",
                    (owner_id,),
                )
                deleted_count = connection.total_changes - before_changes
                if deleted_count < len(delete_ids):
                    raise BaselineError("Disposable drill did not delete every exact target.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            target_memory_count = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE id IN (SELECT id FROM reset_target_ids)"
            ).fetchone()[0]
            target_event_count = 0
            if target_event_ids:
                placeholders = ",".join("?" for _ in target_event_ids)
                target_event_count = connection.execute(
                    f"SELECT COUNT(*) FROM memory_events WHERE id IN ({placeholders})",
                    tuple(sorted(target_event_ids)),
                ).fetchone()[0]
            target_fts_count = connection.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE memory_id IN (SELECT id FROM reset_target_ids)"
            ).fetchone()[0]
            if target_memory_count or target_event_count or target_fts_count:
                raise BaselineError("Disposable drill left target memory dependencies behind.")
            owned_dependencies_after = _memory_owned_dependency_state(connection)
            for table, rows in owned_dependencies_after.items():
                if any(str(row.get("memory_id") or "") in target_set for row in rows):
                    raise BaselineError(f"Disposable drill left target rows in {table}.")
                if rows != retained_dependencies_before[table]:
                    raise BaselineError(f"Disposable drill changed retained rows in {table}.")
            if _rows(connection, "memory_fts") != retained_fts_before:
                raise BaselineError("Disposable drill changed retained memory search-index rows.")

            _all_memories_after, _all_events_after, all_fingerprints_after = _all_database_memory_state(connection)
            if all_fingerprints_after != retained_fingerprints_before:
                raise BaselineError("Disposable drill changed retained memory rows or history.")
            protected_after = _protected_non_memory_hashes(connection)
            if protected_after != protected_before:
                raise BaselineError("Disposable drill changed protected non-memory data.")
            persona_after = _persona_inventory(connection, owner_id)
            if persona_after["sha256"] != persona_inventory["sha256"]:
                raise BaselineError("Disposable drill changed persona definitions.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BaselineError("Disposable drill failed its foreign-key check.")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]) != "ok":
                raise BaselineError("Disposable drill failed its integrity check.")
            return {
                "ok": True,
                "mode": "disposable_snapshot_copy_only",
                "schema_revision": extracted.schema_revision,
                "snapshot_sha256": extracted.snapshot_sha256,
                "reset_plan_sha256": plan["plan_sha256"],
                "target_memory_count": len(delete_ids),
                "quarantined_or_retained_count": len(owner_memory_ids) - len(delete_ids),
                "cascaded_event_count": len(target_event_ids),
                "protected_non_memory_sha256": protected_after["sha256"],
                "persona_inventory_sha256": persona_after["sha256"],
                "source_snapshot_unchanged": True,
                "live_execution_supported": False,
            }
        finally:
            connection.close()


__all__ = [
    "BaselineError",
    "BaselineExportResult",
    "CURRENT_SCHEMA_REVISION",
    "drill_memory_reset",
    "export_memory_baseline",
    "extracted_snapshot_database",
]
