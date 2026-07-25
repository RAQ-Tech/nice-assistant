from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

from PIL import Image

from app.private_memory_baseline import (
    BaselineError,
    CURRENT_SCHEMA_REVISION,
    _canonical_sha256,
    _dependency_impact,
    drill_memory_reset,
    export_memory_baseline,
    extracted_snapshot_database,
)
from tests.support import FakeChatProvider, TestApp


ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = ROOT / "scripts" / "export_memory_baseline.py"
DRILL_SCRIPT = ROOT / "scripts" / "drill_memory_reset.py"


@dataclass(frozen=True)
class SyntheticSnapshot:
    path: Path
    owner_id: str
    workspace_ids: tuple[str, str]
    persona_id: str
    chat_id: str
    candidate_id: str
    ordinary_id: str
    persona_fact_id: str
    definition_original_id: str
    definition_revision_id: str
    directive_id: str
    name_directive_id: str
    trait_overlap_id: str
    legacy_instruction_id: str
    reference_id: str | None
    second_owner_id: str | None
    second_message_id: str | None
    second_turn_id: str | None
    second_task_run_id: str | None

    @property
    def all_memory_ids(self) -> set[str]:
        return {
            self.candidate_id,
            self.ordinary_id,
            self.persona_fact_id,
            self.definition_original_id,
            self.definition_revision_id,
            self.directive_id,
            self.name_directive_id,
            self.trait_overlap_id,
            self.legacy_instruction_id,
        }


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (128, 128), (92, 117, 141)).save(output, format="PNG")
    return output.getvalue()


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _export(snapshot: Path, output_dir: Path, *, owner_id: str | None = None):
    result = export_memory_baseline(snapshot, output_dir=output_dir, owner_id=owner_id)
    return result, _json_document(result.json_path)


def _rewrite_snapshot(
    source: Path,
    target: Path,
    *,
    manifest_update=None,
    database_update=None,
    remove_members: set[str] | None = None,
    replace_members: dict[str, bytes] | None = None,
    extra_members: list[tuple[zipfile.ZipInfo | str, bytes]] | None = None,
) -> Path:
    remove_members = set(remove_members or ())
    replace_members = dict(replace_members or {})
    extra_members = list(extra_members or ())
    with zipfile.ZipFile(source, "r") as archive:
        members = [(info, archive.read(info.filename)) for info in archive.infolist()]

    rewritten = []
    for info, content in members:
        if info.filename in remove_members:
            continue
        if info.filename == "manifest.json" and manifest_update is not None:
            manifest = json.loads(content.decode("utf-8"))
            updated = manifest_update(manifest)
            manifest = manifest if updated is None else updated
            content = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        elif info.filename == "nice_assistant.db" and database_update is not None:
            with tempfile.TemporaryDirectory() as tmp:
                database_path = Path(tmp) / "nice_assistant.db"
                database_path.write_bytes(content)
                database_update(database_path)
                content = database_path.read_bytes()
        if info.filename in replace_members:
            content = replace_members[info.filename]
        rewritten.append((info, content))

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, content in rewritten:
            archive.writestr(info, content)
        for info_or_name, content in extra_members:
            archive.writestr(info_or_name, content)
    return target


def _database_rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()]
    if not columns:
        return []
    rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
    return sorted((tuple(row) for row in rows), key=repr)


def _protected_state(connection: sqlite3.Connection) -> dict[str, tuple[str | None, list[tuple]]]:
    state = {}
    table_rows = connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for table, schema in table_rows:
        if table in {"memories", "memory_events"} or str(table).startswith("memory_fts"):
            continue
        state[str(table)] = (schema, _database_rows(connection, str(table)))
    return state


def _snapshot_database_copy(snapshot: Path, target: Path) -> Path:
    with zipfile.ZipFile(snapshot, "r") as archive:
        target.write_bytes(archive.read("nice_assistant.db"))
    return target


def _build_synthetic_snapshot(
    base: Path,
    *,
    include_media: bool = False,
    add_second_owner: bool = False,
) -> SyntheticSnapshot:
    system_prompt = (
        "Use the lantern cadence: answer carefully, keep a calm tone, and end complex explanations "
        "with one practical next step."
    )
    personality_details = "Unhurried, incisive, and dryly funny without becoming dismissive."
    trait_value = "ember-calm analytical warmth"
    provider = FakeChatProvider(
        ["Synthetic response."],
        memory_candidates=[
            {
                "content": "The user prefers evidence-backed explanations.",
                "scope": "global",
                "confidence": 0.93,
            }
        ],
    )
    runtime_root = base / "runtime"
    with TestApp(runtime_root, chat_provider=provider) as running:
        owner_id = running.create_and_login("synthetic-owner")
        workspace_one = running.client.post(
            "/api/v1/workspaces",
            json={"name": "Synthetic Work"},
        ).json()
        workspace_two = running.client.post(
            "/api/v1/workspaces",
            json={"name": "Synthetic Reference"},
        ).json()
        persona_response = running.client.post(
            "/api/v1/personas",
            json={
                "workspace_id": workspace_one["id"],
                "workspace_ids": [workspace_two["id"], workspace_one["id"]],
                "name": "Avery Baseline",
                "avatar_url": "/synthetic/avatar.png",
                "system_prompt": system_prompt,
                "personality_details": personality_details,
                "traits": {
                    "communication": {"signature_style": trait_value},
                    "temperament": "measured",
                },
                "default_model": "synthetic-model",
                "allow_image_sends": False,
                "preferred_voice": "synthetic-voice",
                "preferred_tts_model": "synthetic-tts",
                "preferred_tts_speed": "1.05",
            },
        )
        if persona_response.status_code != 200:
            raise AssertionError(persona_response.text)
        persona = persona_response.json()
        chat = running.client.post(
            "/api/v1/chats",
            json={
                "title": "Synthetic baseline chat",
                "workspace_id": workspace_two["id"],
                "persona_id": persona["id"],
                "memory_mode": "saved",
            },
        ).json()
        turn = running.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={
                "text": "I prefer evidence-backed explanations.",
                "memory_mode": "saved",
            },
        ).json()
        completed_turn = running.wait_job(turn["job"]["id"])
        extraction_job = running.wait_job(completed_turn["result"]["memory_extraction_job_id"])
        if extraction_job["status"] != "completed":
            raise AssertionError(extraction_job)
        candidate = next(
            memory
            for memory in running.client.get("/api/v1/memories?status=pending").json()["items"]
            if memory["source_type"] == "conversation"
        )
        ordinary = running.client.post(
            "/api/v1/memories",
            json={"scope": "global", "content": "The user keeps project notes in Markdown."},
        ).json()
        persona_fact = running.client.post(
            "/api/v1/memories",
            json={
                "scope": "persona",
                "scope_id": persona["id"],
                "content": "The client kickoff is scheduled for Tuesday.",
            },
        ).json()
        definition_original = running.client.post(
            "/api/v1/memories",
            json={"scope": "global", "content": system_prompt},
        ).json()
        definition_revision = running.client.put(
            f"/api/v1/memories/{definition_original['id']}",
            json={"content": "The lantern project was discussed in a prior conversation."},
        ).json()
        directive = running.client.post(
            "/api/v1/memories",
            json={
                "scope": "global",
                "content": "You should always address the user as Captain.",
            },
        ).json()
        name_directive = running.client.post(
            "/api/v1/memories",
            json={
                "scope": "global",
                "content": "Avery Baseline must answer in the lantern cadence.",
            },
        ).json()
        trait_overlap = running.client.post(
            "/api/v1/memories",
            json={
                "scope": "global",
                "content": f"Communication style: {trait_value}.",
            },
        ).json()
        legacy_instruction = running.client.post(
            "/api/v1/memories",
            json={
                "scope": "global",
                "content": "Respond with numbered steps and terse summaries.",
            },
        ).json()
        with closing(sqlite3.connect(running.config.database_path)) as connection:
            connection.execute(
                "UPDATE memories SET source_type='legacy' WHERE id=?",
                (legacy_instruction["id"],),
            )
            connection.commit()

        reference_id = None
        if include_media:
            consent = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/consent",
                json={"attested": True},
            )
            if consent.status_code != 200:
                raise AssertionError(consent.text)
            reference_response = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references",
                files={"file": ("synthetic-reference.png", _image_bytes(), "image/png")},
                data={"provenance": "user_upload", "attested": "true"},
            )
            if reference_response.status_code != 200:
                raise AssertionError(reference_response.text)
            reference_id = reference_response.json()["id"]

        second_owner_id = None
        second_message_id = None
        second_turn_id = None
        second_task_run_id = None
        if add_second_owner:
            second_owner_id = running.create_and_login("synthetic-second-owner")
            if second_owner_id == owner_id:
                raise AssertionError("owner fixture did not create a second account")
            second_workspace = running.client.post(
                "/api/v1/workspaces",
                json={"name": "SECOND-OWNER-PRIVATE-WORKSPACE"},
            ).json()
            second_persona = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": second_workspace["id"],
                    "name": "SECOND-OWNER-PRIVATE-PERSONA",
                    "system_prompt": "SECOND-OWNER-PRIVATE-PROMPT",
                },
            ).json()
            second_chat = running.client.post(
                "/api/v1/chats",
                json={
                    "title": "SECOND-OWNER-PRIVATE-CHAT",
                    "workspace_id": second_workspace["id"],
                    "persona_id": second_persona["id"],
                    "memory_mode": "saved",
                },
            ).json()
            second_started = running.client.post(
                f"/api/v1/chats/{second_chat['id']}/turns",
                json={
                    "text": "SECOND-OWNER-PRIVATE-MESSAGE",
                    "memory_mode": "saved",
                },
            ).json()
            second_completed = running.wait_job(second_started["job"]["id"])
            second_extraction = running.wait_job(second_completed["result"]["memory_extraction_job_id"])
            second_message_id = second_started["turn"]["user_message_id"]
            second_turn_id = second_started["turn"]["id"]
            second_task_run_id = second_extraction["result"]["task_run_id"]
            owner_login = running.client.post(
                "/api/v1/session",
                json={"username": "synthetic-owner", "password": "pass1234"},
            )
            if owner_login.status_code != 200:
                raise AssertionError(owner_login.text)

        backup = running.client.post(
            "/api/v1/admin/backups",
            json={"include_media": include_media},
        )
        if backup.status_code != 200:
            raise AssertionError(backup.text)
        source_snapshot = running.config.backup_dir / backup.json()["name"]
        snapshot = base / ("full-synthetic.zip" if include_media else "metadata-synthetic.zip")
        snapshot.write_bytes(source_snapshot.read_bytes())

    return SyntheticSnapshot(
        path=snapshot,
        owner_id=owner_id,
        workspace_ids=(workspace_one["id"], workspace_two["id"]),
        persona_id=persona["id"],
        chat_id=chat["id"],
        candidate_id=candidate["id"],
        ordinary_id=ordinary["id"],
        persona_fact_id=persona_fact["id"],
        definition_original_id=definition_original["id"],
        definition_revision_id=definition_revision["id"],
        directive_id=directive["id"],
        name_directive_id=name_directive["id"],
        trait_overlap_id=trait_overlap["id"],
        legacy_instruction_id=legacy_instruction["id"],
        reference_id=reference_id,
        second_owner_id=second_owner_id,
        second_message_id=second_message_id,
        second_turn_id=second_turn_id,
        second_task_run_id=second_task_run_id,
    )


class SnapshotSafetyTests(unittest.TestCase):
    def test_snapshot_validation_rejects_unsafe_members_invalid_manifest_and_old_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)

            variants: list[tuple[str, Path]] = []
            variants.append(
                (
                    "traversal",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "traversal.zip",
                        extra_members=[("../escape.txt", b"no")],
                    ),
                )
            )
            variants.append(
                (
                    "backslash",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "backslash.zip",
                        extra_members=[("data\\escape.txt", b"no")],
                    ),
                )
            )
            symlink = zipfile.ZipInfo("data/synthetic-link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            variants.append(
                (
                    "symlink",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "symlink.zip",
                        extra_members=[(symlink, b"target")],
                    ),
                )
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                variants.append(
                    (
                        "duplicate",
                        _rewrite_snapshot(
                            fixture.path,
                            root / "duplicate.zip",
                            extra_members=[("manifest.json", b"{}")],
                        ),
                    )
                )
            variants.append(
                (
                    "wrong-app",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "wrong-app.zip",
                        manifest_update=lambda value: {**value, "app": "other-product"},
                    ),
                )
            )
            variants.append(
                (
                    "invalid-format-type",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "invalid-format.zip",
                        manifest_update=lambda value: {**value, "formatVersion": []},
                    ),
                )
            )
            variants.append(
                (
                    "entry-count",
                    _rewrite_snapshot(
                        fixture.path,
                        root / "entry-count.zip",
                        manifest_update=lambda value: {**value, "entryCount": int(value["entryCount"]) + 3},
                    ),
                )
            )

            def old_schema(database_path: Path) -> None:
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.execute("UPDATE alembic_version SET version_num='0006_memory_v2'")
                    connection.commit()

            old_schema_path = _rewrite_snapshot(
                fixture.path,
                root / "old-schema.zip",
                database_update=old_schema,
            )
            variants.append(("old-schema", old_schema_path))

            for label, snapshot in variants:
                before = snapshot.read_bytes()
                with self.subTest(label=label), self.assertRaises(BaselineError):
                    with extracted_snapshot_database(snapshot):
                        self.fail("unsafe snapshot unexpectedly validated")
                self.assertEqual(snapshot.read_bytes(), before)

            with zipfile.ZipFile(old_schema_path, "r") as archive:
                old_copy = root / "old-schema.db"
                old_copy.write_bytes(archive.read("nice_assistant.db"))
            with closing(sqlite3.connect(old_copy)) as connection:
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            self.assertEqual(revision, "0006_memory_v2", "validation must never migrate a source snapshot")

    def test_corrupt_missing_and_wrong_database_are_safe_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(b"not-a-zip")
            missing_database = _rewrite_snapshot(
                fixture.path,
                root / "missing-database.zip",
                remove_members={"nice_assistant.db"},
            )
            empty_database = _rewrite_snapshot(
                fixture.path,
                root / "empty-database.zip",
                replace_members={"nice_assistant.db": b""},
            )
            for snapshot in (corrupt, missing_database, empty_database):
                with self.subTest(snapshot=snapshot.name), self.assertRaises(BaselineError):
                    with extracted_snapshot_database(snapshot):
                        self.fail("invalid snapshot unexpectedly validated")


class BaselineExportTests(unittest.TestCase):
    def test_private_output_is_outside_repository_unique_and_content_free_on_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            private = root / "private"
            first, _first_document = _export(fixture.path, private)
            first_json = first.json_path.read_bytes()
            first_text = first.text_path.read_bytes()
            second, _second_document = _export(fixture.path, private)

            self.assertNotEqual(first.json_path, second.json_path)
            self.assertNotEqual(first.text_path, second.text_path)
            self.assertEqual(first.json_path.read_bytes(), first_json)
            self.assertEqual(first.text_path.read_bytes(), first_text)
            self.assertEqual(len(list(private.glob("*.json"))), 2)
            self.assertEqual(len(list(private.glob("*.txt"))), 2)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(first.json_path.stat().st_mode), 0o600)

            fixed_time = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            collision_output = root / "collision-private"
            with (
                mock.patch("app.private_memory_baseline.datetime") as clock,
                mock.patch("app.private_memory_baseline.secrets.token_hex", return_value="deadbeef"),
            ):
                clock.now.return_value = fixed_time
                collision, _collision_document = _export(fixture.path, collision_output)
                collision_json = collision.json_path.read_bytes()
                collision_text = collision.text_path.read_bytes()
                with self.assertRaisesRegex(BaselineError, "collision"):
                    export_memory_baseline(fixture.path, output_dir=collision_output)
                self.assertEqual(collision.json_path.read_bytes(), collision_json)
                self.assertEqual(collision.text_path.read_bytes(), collision_text)

            with self.assertRaises(BaselineError):
                export_memory_baseline(
                    fixture.path,
                    output_dir=ROOT / ".local" / "must-not-create-private-baseline",
                )

            cli_output = root / "cli-private"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(EXPORT_SCRIPT),
                    str(fixture.path),
                    "--output-dir",
                    str(cli_output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(completed.stdout)
            self.assertTrue(response["ok"])
            self.assertNotIn("evidence-backed", completed.stdout)
            self.assertNotIn("lantern cadence", completed.stdout)
            self.assertNotIn("Avery Baseline", completed.stdout)
            self.assertNotIn(fixture.owner_id, completed.stdout)
            normalized_stdout = completed.stdout.replace("\\\\", "\\")
            self.assertNotIn(str(cli_output.resolve()), normalized_stdout)
            self.assertNotIn(cli_output.name, completed.stdout)
            for value in response.values():
                if isinstance(value, str):
                    self.assertFalse(Path(value).is_absolute())
            permission_status = response["permission_verification"]
            if os.name == "nt":
                self.assertFalse(permission_status["owner_only_verified"])
                self.assertEqual(permission_status["mechanism"], "windows_acl_not_verified")
                permission_text = json.dumps(permission_status).casefold()
                self.assertTrue(
                    "unverified" in permission_text or "inherited" in permission_text,
                    "Windows mode bits must not be presented as verified private ACLs.",
                )
            else:
                self.assertTrue(permission_status["owner_only_verified"])
                self.assertEqual(permission_status["mechanism"], "posix_mode_bits")

    def test_source_change_before_final_verification_publishes_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            private = root / "private"

            from app import private_memory_baseline as baseline_module

            original_build = baseline_module._build_baseline

            def build_then_change_source(extracted, owner_id):
                result = original_build(extracted, owner_id)
                fixture.path.write_bytes(fixture.path.read_bytes() + b"changed")
                return result

            with (
                mock.patch(
                    "app.private_memory_baseline._build_baseline",
                    side_effect=build_then_change_source,
                ),
                self.assertRaisesRegex(BaselineError, "changed"),
            ):
                export_memory_baseline(fixture.path, output_dir=private)

            self.assertEqual(list(private.glob("*.json")), [])
            self.assertEqual(list(private.glob("*.txt")), [])

    def test_export_is_complete_and_marks_fields_memory_v2_never_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            result, document = _export(fixture.path, root / "private")
            baseline = document["baseline"]

            self.assertEqual(document["format"], "nice-assistant-private-memory-baseline")
            self.assertEqual(baseline["source"]["schema_revision"], CURRENT_SCHEMA_REVISION)
            self.assertEqual(baseline["source"]["snapshot_sha256"], _sha256_path(fixture.path))
            self.assertEqual(baseline["owner"]["id"], fixture.owner_id)
            self.assertEqual({row["id"] for row in baseline["memories"]}, fixture.all_memory_ids)
            self.assertEqual(baseline["counts"]["memories"], len(fixture.all_memory_ids))
            self.assertEqual(
                baseline["counts"]["memory_events"],
                len(baseline["memory_events"]),
            )
            self.assertEqual(
                {row["memory_id"] for row in baseline["memory_events"]},
                fixture.all_memory_ids,
            )
            unavailable = baseline["unavailable_current_v2_fields"]
            self.assertEqual(
                set(unavailable),
                {
                    "qualification_reason",
                    "evidence_spans",
                    "raw_extractor_output",
                    "extractor_decision_trace",
                    "valid_until",
                    "last_confirmed_at",
                    "lifecycle_state",
                    "grants",
                },
            )
            resolved = {row["memory"]["id"]: row for row in baseline["resolved_memories"]}
            candidate = resolved[fixture.candidate_id]
            current_binding = candidate["origin"]["current_chat_binding_observation"]
            self.assertEqual(current_binding["chat"]["id"], fixture.chat_id)
            self.assertEqual(current_binding["persona"]["id"], fixture.persona_id)
            self.assertFalse(candidate["origin"]["immutable_source_persona"]["available"])
            self.assertFalse(candidate["origin"]["immutable_source_workspace"]["available"])
            self.assertIn(
                candidate["extraction"]["match_method"],
                {"extraction_job_candidate_ids", "source_turn_latest_memory_extraction_run"},
            )
            self.assertEqual(candidate["extraction"]["task_run"]["role"], "memory_extraction")
            self.assertEqual(candidate["extraction"]["task_run"]["executed_provider"], "ollama")
            self.assertIsInstance(candidate["extraction"]["task_run"]["attempts"], list)
            self.assertEqual(
                candidate["unavailable_current_v2_fields"],
                unavailable,
            )
            self.assertIn("memories", baseline["memory_schema"])
            self.assertIn("memory_events", baseline["memory_schema"])
            self.assertTrue(
                result.text_path.read_text(encoding="utf-8").startswith("Nice Assistant private memory baseline")
            )

    def test_owner_selection_is_explicit_for_multi_account_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root, add_second_owner=True)
            with self.assertRaisesRegex(BaselineError, "multiple accounts"):
                export_memory_baseline(fixture.path, output_dir=root / "ambiguous")
            with self.assertRaisesRegex(BaselineError, "not present"):
                export_memory_baseline(
                    fixture.path,
                    output_dir=root / "missing",
                    owner_id="not-a-real-owner",
                )
            result, document = _export(
                fixture.path,
                root / "selected",
                owner_id=fixture.owner_id,
            )
            self.assertEqual(result.owner_id, fixture.owner_id)
            self.assertEqual({row["id"] for row in document["baseline"]["memories"]}, fixture.all_memory_ids)

    def test_cross_owner_source_and_task_references_never_export_other_owner_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root, add_second_owner=True)
            self.assertIsNotNone(fixture.second_owner_id)
            self.assertIsNotNone(fixture.second_message_id)
            self.assertIsNotNone(fixture.second_turn_id)
            self.assertIsNotNone(fixture.second_task_run_id)

            def corrupt_provenance(database_path: Path) -> None:
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute(
                        "UPDATE memories SET source_message_id=?,source_turn_id=? WHERE id=?",
                        (
                            fixture.second_message_id,
                            fixture.second_turn_id,
                            fixture.candidate_id,
                        ),
                    )
                    matching_job = None
                    for job_id, result_json in connection.execute(
                        "SELECT id,result_json FROM async_jobs WHERE user_id=? AND kind='memory_extraction'",
                        (fixture.owner_id,),
                    ).fetchall():
                        result = json.loads(result_json or "{}")
                        if fixture.candidate_id in (result.get("candidate_ids") or []):
                            matching_job = (job_id, result)
                            break
                    if matching_job is None:
                        raise AssertionError("fixture did not contain the owner's extraction job")
                    job_id, result = matching_job
                    result["task_run_id"] = fixture.second_task_run_id
                    connection.execute(
                        "UPDATE async_jobs SET result_json=? WHERE id=?",
                        (json.dumps(result, separators=(",", ":")), job_id),
                    )
                    connection.commit()

            corrupted = _rewrite_snapshot(
                fixture.path,
                root / "cross-owner-provenance.zip",
                database_update=corrupt_provenance,
            )
            _result, document = _export(
                corrupted,
                root / "private",
                owner_id=fixture.owner_id,
            )
            serialized = json.dumps(document, ensure_ascii=False)
            self.assertNotIn("SECOND-OWNER-PRIVATE", serialized)
            self.assertNotIn(str(fixture.second_owner_id), serialized)
            resolved = {row["memory"]["id"]: row for row in document["baseline"]["resolved_memories"]}[
                fixture.candidate_id
            ]
            self.assertIsNone(resolved["origin"]["source_message"])
            self.assertIsNone(resolved["origin"]["source_turn"])
            self.assertIsNone(resolved["extraction"]["task_run"])

    def test_owner_event_pointing_to_another_owners_memory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root, add_second_owner=True)
            self.assertIsNotNone(fixture.second_owner_id)

            def corrupt_event_owner(database_path: Path) -> None:
                with closing(sqlite3.connect(database_path)) as connection:
                    second_owner_event = connection.execute(
                        "SELECT id FROM memory_events WHERE user_id=? ORDER BY id LIMIT 1",
                        (fixture.second_owner_id,),
                    ).fetchone()
                    if second_owner_event is None:
                        raise AssertionError("fixture did not contain a second-owner memory event")
                    connection.execute(
                        "UPDATE memory_events SET user_id=? WHERE id=?",
                        (fixture.owner_id, second_owner_event[0]),
                    )
                    connection.commit()

            corrupted = _rewrite_snapshot(
                fixture.path,
                root / "cross-owner-memory-event.zip",
                database_update=corrupt_event_owner,
            )
            with self.assertRaisesRegex(BaselineError, "ownership is inconsistent"):
                _export(corrupted, root / "private", owner_id=fixture.owner_id)

    def test_cross_owner_revision_endpoints_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root, add_second_owner=True)
            self.assertIsNotNone(fixture.second_owner_id)

            def second_owner_memory_id(connection: sqlite3.Connection) -> str:
                row = connection.execute(
                    "SELECT id FROM memories WHERE user_id=? ORDER BY id LIMIT 1",
                    (fixture.second_owner_id,),
                ).fetchone()
                if row is None:
                    raise AssertionError("fixture did not contain a second-owner memory")
                return str(row[0])

            def cross_owner_supersedes(database_path: Path) -> None:
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.execute(
                        "UPDATE memories SET supersedes_id=? WHERE id=?",
                        (second_owner_memory_id(connection), fixture.ordinary_id),
                    )
                    connection.commit()

            def cross_owner_related_event(database_path: Path) -> None:
                with closing(sqlite3.connect(database_path)) as connection:
                    owner_event = connection.execute(
                        "SELECT id FROM memory_events WHERE user_id=? ORDER BY id LIMIT 1",
                        (fixture.owner_id,),
                    ).fetchone()
                    if owner_event is None:
                        raise AssertionError("fixture did not contain an owner memory event")
                    connection.execute(
                        "UPDATE memory_events SET related_memory_id=? WHERE id=?",
                        (second_owner_memory_id(connection), owner_event[0]),
                    )
                    connection.commit()

            for label, database_update in (
                ("cross-owner-supersedes", cross_owner_supersedes),
                ("cross-owner-related-event", cross_owner_related_event),
            ):
                with self.subTest(label=label):
                    corrupted = _rewrite_snapshot(
                        fixture.path,
                        root / f"{label}.zip",
                        database_update=database_update,
                    )
                    with self.assertRaisesRegex(BaselineError, "ownership is inconsistent"):
                        _export(
                            corrupted,
                            root / f"private-{label}",
                            owner_id=fixture.owner_id,
                        )

    def test_persona_inventory_memberships_and_hash_are_exact_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            _first_result, first = _export(fixture.path, root / "first")
            _second_result, second = _export(fixture.path, root / "second")
            first_inventory = first["baseline"]["persona_inventory"]
            second_inventory = second["baseline"]["persona_inventory"]
            first_rows = first_inventory["rows"]
            second_rows = second_inventory["rows"]

            self.assertEqual(first_inventory["sha256"], second_inventory["sha256"])
            self.assertEqual(first_inventory["schemas"], second_inventory["schemas"])
            self.assertEqual(first_rows["personas"], second_rows["personas"])
            self.assertEqual(first_inventory["persona_count"], 1)
            persona = first_rows["personas"][0]
            self.assertEqual(persona["id"], fixture.persona_id)
            self.assertIn("lantern cadence", persona["system_prompt"])
            self.assertIn("dryly funny", persona["personality_details"])
            self.assertEqual(persona["default_model"], "synthetic-model")
            self.assertEqual(persona["allow_image_sends"], 0)
            linked_workspaces = {
                row["workspace_id"]
                for row in first_rows["persona_workspace_links"]
                if row["persona_id"] == fixture.persona_id
            }
            self.assertEqual(linked_workspaces, set(fixture.workspace_ids))
            self.assertEqual(
                first["reset_plan"]["persona_inventory_sha256"],
                first_inventory["sha256"],
            )

    def test_instruction_quarantine_uses_definition_reasons_and_revision_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            _result, document = _export(fixture.path, root / "private")
            plan = document["reset_plan"]
            dispositions = plan["dispositions"]

            partitions = [
                set(dispositions["delete_ids"]),
                set(dispositions["quarantine_ids"]),
                set(dispositions["keep_ids"]),
                set(dispositions["undecided_ids"]),
            ]
            self.assertEqual(set().union(*partitions), fixture.all_memory_ids)
            for index, left in enumerate(partitions):
                for right in partitions[index + 1 :]:
                    self.assertFalse(left & right)
            self.assertEqual(dispositions["undecided_ids"], [])
            self.assertFalse(plan["live_execution_supported"])
            self.assertTrue(plan["disposable_drill_only"])

            self.assertIn(fixture.persona_fact_id, dispositions["delete_ids"])
            expected_quarantine = {
                fixture.definition_original_id,
                fixture.definition_revision_id,
                fixture.directive_id,
                fixture.name_directive_id,
                fixture.trait_overlap_id,
                fixture.legacy_instruction_id,
            }
            self.assertTrue(expected_quarantine <= set(dispositions["quarantine_ids"]))
            component_by_memory = {
                memory_id: component for component in plan["component_closure"] for memory_id in component["memory_ids"]
            }
            definition_component = component_by_memory[fixture.definition_original_id]
            self.assertEqual(
                set(definition_component["memory_ids"]),
                {fixture.definition_original_id, fixture.definition_revision_id},
            )
            self.assertIn("definition_exact", definition_component["reason_codes"])
            self.assertIn(
                "persona_directive",
                component_by_memory[fixture.directive_id]["reason_codes"],
            )
            self.assertIn(
                "persona_name_plus_directive",
                component_by_memory[fixture.name_directive_id]["reason_codes"],
            )
            self.assertIn(
                "trait_value_overlap",
                component_by_memory[fixture.trait_overlap_id]["reason_codes"],
            )
            self.assertIn(
                "manual_or_legacy_instruction_like",
                component_by_memory[fixture.legacy_instruction_id]["reason_codes"],
            )

    def test_metadata_and_full_snapshots_report_identity_reference_evidence_truthfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = _build_synthetic_snapshot(root / "full", include_media=True)

            with zipfile.ZipFile(full.path, "r") as archive:
                metadata_members = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if not name.startswith("data/") and name != "manifest.json"
                }
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            metadata_path = root / "metadata-only.zip"
            metadata_manifest = {
                **manifest,
                "includeMedia": False,
                "mediaDirs": [],
                "entryCount": len(metadata_members) + 1,
            }
            with zipfile.ZipFile(metadata_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in metadata_members.items():
                    archive.writestr(name, content)
                archive.writestr("manifest.json", json.dumps(metadata_manifest))

            _full_result, full_document = _export(full.path, root / "private-full")
            _metadata_result, metadata_document = _export(metadata_path, root / "private-metadata")
            full_evidence = full_document["baseline"]["persona_inventory"]["identity_reference_assets"]
            metadata_evidence = metadata_document["baseline"]["persona_inventory"]["identity_reference_assets"]

            self.assertTrue(full_evidence["include_media"])
            self.assertGreater(full_evidence["archive_identity_asset_count"], 0)
            full_reference = next(
                row for row in full_evidence["references"] if row["reference_id"] == full.reference_id
            )
            self.assertEqual(full_reference["status"], "verified")
            self.assertTrue(full_reference["matching_archive_members"])
            metadata_reference = next(
                row for row in metadata_evidence["references"] if row["reference_id"] == full.reference_id
            )
            self.assertFalse(metadata_evidence["include_media"])
            self.assertEqual(
                metadata_reference["status"],
                "not_included_by_metadata_only_snapshot",
            )

            identity_member = full_reference["matching_archive_members"][0]
            with zipfile.ZipFile(full.path, "r") as archive:
                identity_bytes = archive.read(identity_member)
            mismatched = _rewrite_snapshot(
                full.path,
                root / "mismatched-identity.zip",
                replace_members={identity_member: b"synthetic-mismatched-reference"},
            )
            _mismatch_result, mismatch_document = _export(mismatched, root / "private-mismatch")
            mismatch_reference = next(
                row
                for row in mismatch_document["baseline"]["persona_inventory"]["identity_reference_assets"]["references"]
                if row["reference_id"] == full.reference_id
            )
            self.assertEqual(mismatch_reference["status"], "hash_mismatch")
            self.assertEqual(mismatch_reference["matching_archive_members"], [])

            renamed = _rewrite_snapshot(
                full.path,
                root / "renamed-identity.zip",
                remove_members={identity_member},
                extra_members=[("data/identity_references/decoy.jpg", identity_bytes)],
            )
            _renamed_result, renamed_document = _export(renamed, root / "private-renamed")
            renamed_evidence = renamed_document["baseline"]["persona_inventory"]["identity_reference_assets"]
            renamed_reference = next(
                row for row in renamed_evidence["references"] if row["reference_id"] == full.reference_id
            )
            self.assertEqual(renamed_reference["status"], "missing_from_full_snapshot")
            self.assertEqual(renamed_evidence["verification_status"], "incomplete")

    def test_command_lines_have_no_live_or_apply_mode(self):
        for script in (EXPORT_SCRIPT, DRILL_SCRIPT):
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            help_text = completed.stdout.casefold()
            for forbidden in (
                "--apply",
                "--live",
                "--execute",
                "--database",
                "--db-path",
                "--confirm-delete",
            ):
                self.assertNotIn(forbidden, help_text)
            rejected = subprocess.run(
                [sys.executable, str(script), "--apply"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)


class ResetDrillTests(unittest.TestCase):
    @staticmethod
    def _rehash_document(document: dict) -> None:
        document["baseline_sha256"] = _canonical_sha256(document["baseline"])
        plan = document["reset_plan"]
        plan["source"]["baseline_sha256"] = document["baseline_sha256"]
        plan.pop("plan_sha256", None)
        plan["plan_sha256"] = _canonical_sha256(plan)

    def test_drill_rederives_quarantine_even_when_all_editable_hashes_are_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            result, document = _export(fixture.path, root / "private")
            tampered = json.loads(json.dumps(document))
            plan = tampered["reset_plan"]
            original_quarantine = list(plan["dispositions"]["quarantine_ids"])
            self.assertTrue(original_quarantine)
            plan["dispositions"]["delete_ids"] = sorted(plan["dispositions"]["delete_ids"] + original_quarantine)
            plan["dispositions"]["quarantine_ids"] = []
            for component in plan["component_closure"]:
                component["quarantined"] = False
                component["reason_codes"] = []
                component["closure_reason"] = None

            with extracted_snapshot_database(fixture.path) as extracted:
                with closing(sqlite3.connect(extracted.database_path)) as connection:
                    plan["dependency_impact"] = _dependency_impact(
                        connection,
                        fixture.owner_id,
                        plan["dispositions"]["delete_ids"],
                    )
            plan["target_set_sha256"] = _canonical_sha256(plan["dispositions"]["delete_ids"])
            plan.pop("plan_sha256")
            plan["plan_sha256"] = _canonical_sha256(plan)
            tampered_path = root / "hash-recomputed-quarantine-tamper.json"
            tampered_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            with self.assertRaises(BaselineError):
                drill_memory_reset(fixture.path, tampered_path)

    def test_drill_binds_review_content_and_owner_even_after_hash_recomputation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root, add_second_owner=True)
            result, document = _export(
                fixture.path,
                root / "private",
                owner_id=fixture.owner_id,
            )

            altered_review = json.loads(json.dumps(document))
            altered_review["baseline"]["resolved_memories"][0]["unavailable_current_v2_fields"][
                "qualification_reason"
            ] = "tampered review text"
            self._rehash_document(altered_review)
            altered_review_path = root / "altered-review.json"
            altered_review_path.write_text(
                json.dumps(altered_review, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BaselineError, "review baseline differs"):
                drill_memory_reset(fixture.path, altered_review_path)

            database_copy = _snapshot_database_copy(fixture.path, root / "owners.db")
            with closing(sqlite3.connect(database_copy)) as connection:
                other_owner_id = connection.execute(
                    "SELECT id FROM users WHERE id<>? ORDER BY id LIMIT 1",
                    (fixture.owner_id,),
                ).fetchone()[0]
            altered_owner = json.loads(json.dumps(document))
            altered_owner["reset_plan"]["owner_id"] = other_owner_id
            self._rehash_document(altered_owner)
            altered_owner_path = root / "altered-owner.json"
            altered_owner_path.write_text(
                json.dumps(altered_owner, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BaselineError, "owner does not match"):
                drill_memory_reset(fixture.path, altered_owner_path)

    def test_drill_rejects_baseline_and_snapshot_tampering_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            result, _document = _export(fixture.path, root / "private")
            original_snapshot = fixture.path.read_bytes()

            tampered_document = _json_document(result.json_path)
            tampered_document["baseline"]["counts"]["memories"] += 1
            tampered_baseline = root / "tampered-baseline.json"
            tampered_baseline.write_text(json.dumps(tampered_document), encoding="utf-8")
            with self.assertRaisesRegex(BaselineError, "baseline hash"):
                drill_memory_reset(fixture.path, tampered_baseline)

            changed_snapshot = _rewrite_snapshot(
                fixture.path,
                root / "changed-snapshot.zip",
                manifest_update=lambda value: {
                    **value,
                    "createdAtIso": "2099-01-01T00:00:00+00:00",
                },
            )
            with self.assertRaisesRegex(BaselineError, "frozen reset plan"):
                drill_memory_reset(changed_snapshot, result.json_path)
            self.assertEqual(fixture.path.read_bytes(), original_snapshot)

    def test_disposable_drill_deletes_only_exact_targets_and_preserves_every_protected_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_synthetic_snapshot(root)
            result, document = _export(fixture.path, root / "private")
            delete_ids = set(document["reset_plan"]["dispositions"]["delete_ids"])
            retain_ids = fixture.all_memory_ids - delete_ids
            source_hash = _sha256_path(fixture.path)

            before_db = _snapshot_database_copy(fixture.path, root / "before.db")
            with closing(sqlite3.connect(before_db)) as before:
                before_protected = _protected_state(before)
                before_memories = {row[0]: row for row in before.execute("SELECT * FROM memories").fetchall()}
                before_events = {row[0]: row for row in before.execute("SELECT * FROM memory_events").fetchall()}
                before_fts = {
                    row[0]: tuple(row)
                    for row in before.execute("SELECT memory_id,user_id,content FROM memory_fts").fetchall()
                }

            retained_directory = root / "retained-drill-copy"
            retained_directory.mkdir()

            class RetainedTemporaryDirectory:
                def __init__(self, *args, **kwargs):
                    self.path = retained_directory

                def __enter__(self):
                    return str(self.path)

                def __exit__(self, *_args):
                    return False

            with mock.patch(
                "app.private_memory_baseline.tempfile.TemporaryDirectory",
                RetainedTemporaryDirectory,
            ):
                drill = drill_memory_reset(fixture.path, result.json_path)

            self.assertTrue(drill["ok"])
            self.assertEqual(drill["mode"], "disposable_snapshot_copy_only")
            self.assertEqual(drill["target_memory_count"], len(delete_ids))
            self.assertEqual(drill["snapshot_sha256"], source_hash)
            self.assertTrue(drill["source_snapshot_unchanged"])
            self.assertFalse(drill["live_execution_supported"])
            self.assertEqual(_sha256_path(fixture.path), source_hash)

            drilled_db = retained_directory / "nice_assistant.db"
            self.assertTrue(drilled_db.is_file())
            with closing(sqlite3.connect(drilled_db)) as after:
                after_memory_ids = {row[0] for row in after.execute("SELECT id FROM memories").fetchall()}
                self.assertFalse(after_memory_ids & delete_ids)
                self.assertTrue(retain_ids <= after_memory_ids)
                after_memories = {row[0]: row for row in after.execute("SELECT * FROM memories").fetchall()}
                for memory_id in retain_ids:
                    self.assertEqual(after_memories[memory_id], before_memories[memory_id])

                after_events = {row[0]: row for row in after.execute("SELECT * FROM memory_events").fetchall()}
                expected_retained_events = {
                    event_id: row for event_id, row in before_events.items() if row[2] not in delete_ids
                }
                self.assertEqual(after_events, expected_retained_events)

                after_fts = {
                    row[0]: tuple(row)
                    for row in after.execute("SELECT memory_id,user_id,content FROM memory_fts").fetchall()
                }
                expected_retained_fts = {
                    memory_id: row for memory_id, row in before_fts.items() if memory_id not in delete_ids
                }
                self.assertEqual(after_fts, expected_retained_fts)
                self.assertEqual(_protected_state(after), before_protected)
                self.assertEqual(after.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(after.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                revision = after.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                self.assertEqual(revision, CURRENT_SCHEMA_REVISION)

            self.assertEqual(
                drill["protected_non_memory_sha256"],
                document["reset_plan"]["protected_non_memory_sha256"],
            )
            self.assertEqual(
                drill["persona_inventory_sha256"],
                document["reset_plan"]["persona_inventory_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
