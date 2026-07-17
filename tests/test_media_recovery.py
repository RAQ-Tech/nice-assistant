import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from alembic import command
from alembic.config import Config

from app import database
from app.provider_contracts import MediaArtifact
from app.repositories import UnitOfWork
from tests.support import TestApp


ROOT = Path(__file__).resolve().parents[1]


def migration_config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
    return config


def upgrade(path: Path, revision: str) -> None:
    engine = database.build_engine(path)
    with engine.begin() as connection:
        config = migration_config(path)
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
    engine.dispose()


class FakeImageProvider:
    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class MediaRecoveryTests(unittest.TestCase):
    def test_migration_removes_image_approval_and_recovers_only_proven_chat_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "nice_assistant.db"
            image_dir = base / "images"
            video_dir = base / "videos"
            identity_dir = base / "identity_references"
            image_dir.mkdir()
            video_dir.mkdir()
            identity_dir.mkdir()
            completed_file = image_dir / "completed.png"
            cancelled_file = image_dir / "cancelled.png"
            legacy_file = image_dir / "legacy.png"
            unproven_file = image_dir / "unproven.png"
            outside_file = identity_dir / "outside.png"
            for target in (completed_file, cancelled_file, legacy_file, unproven_file):
                target.write_bytes(target.stem.encode())
                os.chmod(target, 0o600)
            outside_file.write_bytes(b"outside")
            os.chmod(outside_file, 0o600)
            outside_mode = stat.S_IMODE(outside_file.stat().st_mode)

            upgrade(path, "0017_chat_attachments")
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','hash',1,1)"
            )
            conn.execute(
                "INSERT INTO app_settings(user_id,preferences_json) VALUES('u',?)",
                (json.dumps({"image_confirmation_policy": "always_ask", "kept": True}),),
            )
            conn.execute(
                "INSERT INTO setting_values(user_id,key,value_type,value_json,updated_at) "
                "VALUES('u','image_confirmation_policy','str','\"always_ask\"',1)"
            )
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','Home',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','Guide','{}',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','w','p','Chat',1,1)"
            )
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('user','c','user','hello',1)")
            conn.execute(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('late','c','user','later message',20)"
            )
            requests = [
                ("pending-image", "media.generate_image", "pending_confirmation", None, 2),
                ("pending-video", "media.generate_video", "pending_confirmation", None, 3),
                (
                    "completed-image",
                    "media.generate_image",
                    "completed",
                    json.dumps({"mediaId": "completed-media"}),
                    4,
                ),
                (
                    "cancelled-image",
                    "media.generate_image",
                    "cancelled",
                    json.dumps({"mediaId": "cancelled-media"}),
                    5,
                ),
                (
                    "missing-image",
                    "media.generate_image",
                    "completed",
                    json.dumps({"mediaId": "missing-media"}),
                    6,
                ),
            ]
            for request_id, key, status_value, result_json, requested_at in requests:
                conn.execute(
                    "INSERT INTO capability_requests("
                    "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,"
                    "idempotency_key,result_json,requested_at,completed_at"
                    ") VALUES(?,?,?,?,? ,?,?,?,?,?,?)",
                    (
                        request_id,
                        "u",
                        "c",
                        key,
                        "{}",
                        status_value,
                        "confirm",
                        f"legacy:{request_id}",
                        result_json,
                        requested_at,
                        requested_at if status_value in {"completed", "cancelled"} else None,
                    ),
                )
            media_rows = [
                ("completed-media", completed_file, 4),
                ("cancelled-media", cancelled_file, 5),
                ("missing-media", image_dir / "missing.png", 6),
                ("legacy-media", legacy_file, 7),
                ("unproven-media", unproven_file, 8),
                ("outside-media", outside_file, 9),
            ]
            for media_id, local_path, created_at in media_rows:
                conn.execute(
                    "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (media_id, "u", "c", "image", local_path.name, str(local_path), created_at),
                )
            conn.execute(
                "INSERT INTO async_jobs("
                "id,user_id,chat_id,kind,status,cancel_requested,created_at,updated_at,completed_at,result_json"
                ") VALUES('legacy-job','u','c','image','completed',0,7,7,7,?)",
                (json.dumps({"mediaId": "legacy-media"}),),
            )
            conn.commit()
            conn.close()

            upgrade(path, "head")
            conn = database.connect_sqlite(path)
            self.assertEqual(
                conn.execute("SELECT allow_image_sends FROM personas WHERE id='p'").fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE personas SET allow_image_sends=2 WHERE id='p'")
            preferences = json.loads(
                conn.execute("SELECT preferences_json FROM app_settings WHERE user_id='u'").fetchone()[0]
            )
            self.assertEqual(preferences, {"kept": True})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM setting_values WHERE key='image_confirmation_policy'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT status FROM capability_requests WHERE id='pending-image'").fetchone()[0],
                "cancelled",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,retry_available FROM chat_attachments "
                        "WHERE capability_request_id='pending-image'"
                    ).fetchone()
                ),
                ("cancelled", 1),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM capability_requests WHERE id='pending-video'").fetchone()[0],
                "pending_confirmation",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM chat_attachments WHERE capability_request_id='pending-video'"
                ).fetchone()[0],
                "queued",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id FROM chat_attachments WHERE capability_request_id='completed-image'"
                    ).fetchone()
                ),
                ("completed", "completed-media"),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id FROM chat_attachments WHERE capability_request_id='cancelled-image'"
                    ).fetchone()
                ),
                ("cancelled", None),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available FROM chat_attachments "
                        "WHERE capability_request_id='missing-image'"
                    ).fetchone()
                ),
                ("failed", None, 1),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,error_code FROM capability_requests WHERE id='missing-image'"
                    ).fetchone()
                ),
                ("failed", "artifact_missing"),
            )
            legacy_request_id = conn.execute(
                "SELECT capability_request_id FROM async_jobs WHERE id='legacy-job'"
            ).fetchone()[0]
            self.assertIsNotNone(legacy_request_id)
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id FROM chat_attachments WHERE capability_request_id=?",
                        (legacy_request_id,),
                    ).fetchone()
                ),
                ("completed", "legacy-media"),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM chat_attachments WHERE media_id='unproven-media'").fetchone()[0],
                0,
            )
            legacy_message_time = conn.execute(
                "SELECT messages.created_at FROM chat_attachments "
                "JOIN messages ON messages.id=chat_attachments.assistant_message_id "
                "WHERE chat_attachments.capability_request_id=?",
                (legacy_request_id,),
            ).fetchone()[0]
            self.assertEqual(legacy_message_time, 7)
            self.assertLess(legacy_message_time, 20)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM capability_events "
                    "WHERE capability_request_id='pending-image' AND action='cancelled'"
                ).fetchone()[0],
                1,
            )
            before = conn.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0]
            conn.close()

            module_path = ROOT / "migrations" / "versions" / "0018_human_image_delivery.py"
            spec = importlib.util.spec_from_file_location("migration_0018_for_test", module_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            engine = database.build_engine(path)
            with engine.begin() as connection:
                module._backfill_legacy_attachments(connection, 20)
                module._cancel_legacy_image_approvals(connection, 20)
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0], before)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM capability_events "
                    "WHERE capability_request_id='pending-image' AND action='cancelled'"
                ).fetchone()[0],
                1,
            )
            conn.close()
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(completed_file.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(outside_file.stat().st_mode), outside_mode)

    def test_migration_recovers_interrupted_outputs_without_bypassing_identity_or_duplicating_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "nice_assistant.db"
            image_dir = base / "images"
            image_dir.mkdir()
            embedded_path = image_dir / "embedded.png"
            recovered_path = image_dir / "recovered.png"
            strict_path = image_dir / "strict.png"
            strict_raw_path = image_dir / "strict-raw.png"
            generic_path = image_dir / "generic.png"
            for target in (embedded_path, recovered_path, strict_path, strict_raw_path, generic_path):
                target.write_bytes(target.stem.encode())

            upgrade(path, "0017_chat_attachments")
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','hash',1,1)"
            )
            conn.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at) VALUES('c','u','Chat',1,1)")
            conn.executemany(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                [
                    ("embedded-user", "c", "user", "show me", 1),
                    (
                        "embedded-assistant",
                        "c",
                        "assistant",
                        "Here it is.\n\n![Generated image](/api/v1/media/embedded-media)",
                        2,
                    ),
                    ("recovered-assistant", "c", "assistant", "", 3),
                    ("strict-assistant", "c", "assistant", "", 4),
                    ("missing-assistant", "c", "assistant", "", 5),
                    ("strict-raw-assistant", "c", "assistant", "", 6),
                    ("generic-assistant", "c", "assistant", "", 7),
                ],
            )
            conn.execute(
                "INSERT INTO conversation_turns("
                "id,user_id,chat_id,user_message_id,assistant_message_id,sequence_number,"
                "provider,model,status,created_at,started_at,completed_at"
                ") VALUES('embedded-turn','u','c','embedded-user','embedded-assistant',1,"
                "'ollama','fake','completed',1,1,2)"
            )
            request_rows = [
                (
                    "embedded-request",
                    "embedded-turn",
                    "completed",
                    json.dumps({"mediaId": "embedded-media"}),
                    None,
                    None,
                    2,
                ),
                ("recovered-request", None, "failed", None, "interrupted", "restart", 3),
                ("strict-request", None, "failed", None, "interrupted", "restart", 4),
                (
                    "missing-request",
                    None,
                    "completed",
                    json.dumps({"mediaId": "missing-media"}),
                    None,
                    None,
                    5,
                ),
                ("strict-raw-request", None, "failed", None, "interrupted", "restart", 6),
                ("generic-request", None, "failed", None, "interrupted", "restart", 7),
            ]
            for request_id, turn_id, status_value, result_json, error_code, error_message, stamp in request_rows:
                conn.execute(
                    "INSERT INTO capability_requests("
                    "id,user_id,chat_id,turn_id,capability_key,arguments_json,status,permission_mode,"
                    "idempotency_key,result_json,error_code,error_message,requested_at,completed_at"
                    ") VALUES(?,?,?,?,?,'{}',?,'explicit',?,?,?,?,?,?)",
                    (
                        request_id,
                        "u",
                        "c",
                        turn_id,
                        "media.generate_image",
                        status_value,
                        f"migration-edge:{request_id}",
                        result_json,
                        error_code,
                        error_message,
                        stamp,
                        stamp,
                    ),
                )
            for plan_id, request_id, conditioning, stamp in (
                (
                    "recovered-plan",
                    "recovered-request",
                    {"status": "unconditioned", "failure_policy": "block_claim"},
                    3,
                ),
                (
                    "strict-plan",
                    "strict-request",
                    {"status": "ready", "failure_policy": "block_claim"},
                    4,
                ),
                (
                    "strict-raw-plan",
                    "strict-raw-request",
                    {"status": "ready", "failure_policy": "block_claim"},
                    6,
                ),
            ):
                conn.execute(
                    "INSERT INTO media_execution_plans("
                    "id,user_id,capability_request_id,source,status,kind,operation,requirements_json,"
                    "selected_resources_json,execution_options_json,explanation_json,"
                    "identity_conditioning_json,created_at"
                    ") VALUES(?,?,?,'coordinator','ready','image','generate','{}','[]','{}','{}',?,?)",
                    (plan_id, "u", request_id, json.dumps(conditioning), stamp),
                )
            conn.execute(
                "INSERT INTO media_execution_plans("
                "id,user_id,capability_request_id,source,status,kind,operation,requirements_json,"
                "selected_resources_json,execution_options_json,explanation_json,"
                "identity_conditioning_json,created_at"
                ") VALUES('generic-plan','u','generic-request','manual','ready','image','generate',"
                "'{}','[]','{}','{}','{}',7)"
            )
            media_rows = [
                ("embedded-media", embedded_path, None, 2),
                ("recovered-media", recovered_path, "recovered-plan", 3),
                ("strict-media", strict_path, "strict-plan", 4),
                ("missing-media", image_dir / "deleted.png", None, 5),
                ("strict-raw-media", strict_raw_path, "strict-raw-plan", 6),
                ("generic-media", generic_path, "generic-plan", 7),
            ]
            for media_id, local_path, plan_id, stamp in media_rows:
                conn.execute(
                    "INSERT INTO media_files("
                    "id,user_id,chat_id,kind,filename,local_path,generation_plan_id,created_at"
                    ") VALUES(?,?,?,'image',?,?,?,?)",
                    (media_id, "u", "c", local_path.name, str(local_path), plan_id, stamp),
                )
            conn.executemany(
                "INSERT INTO media_generation_attempts("
                "id,user_id,media_plan_id,attempt_number,operation,status,media_id,error_code,"
                "error_message,started_at,completed_at"
                ") VALUES(?,?,?,1,'generate',?,?,?,?,?,?)",
                [
                    (
                        "recovered-attempt",
                        "u",
                        "recovered-plan",
                        "error",
                        "recovered-media",
                        "interrupted",
                        "restart",
                        3,
                        3,
                    ),
                    (
                        "strict-attempt",
                        "u",
                        "strict-plan",
                        "failed",
                        "strict-media",
                        None,
                        None,
                        4,
                        4,
                    ),
                    (
                        "generic-attempt",
                        "u",
                        "generic-plan",
                        "passed",
                        "generic-media",
                        None,
                        None,
                        7,
                        7,
                    ),
                ],
            )
            for attachment_id, request_id, assistant_id, status_value, media_id, identity_state, stamp in (
                (
                    "recovered-attachment",
                    "recovered-request",
                    "recovered-assistant",
                    "failed",
                    None,
                    "not_applicable",
                    3,
                ),
                (
                    "strict-attachment",
                    "strict-request",
                    "strict-assistant",
                    "failed",
                    None,
                    "unverified",
                    4,
                ),
                (
                    "missing-attachment",
                    "missing-request",
                    "missing-assistant",
                    "completed",
                    "missing-media",
                    "not_applicable",
                    5,
                ),
                (
                    "strict-raw-attachment",
                    "strict-raw-request",
                    "strict-raw-assistant",
                    "failed",
                    None,
                    "unverified",
                    6,
                ),
                (
                    "generic-attachment",
                    "generic-request",
                    "generic-assistant",
                    "failed",
                    None,
                    "not_applicable",
                    7,
                ),
            ):
                conn.execute(
                    "INSERT INTO chat_attachments("
                    "id,user_id,chat_id,assistant_message_id,capability_request_id,kind,status,media_id,"
                    "identity_state,retry_available,created_at,updated_at,completed_at"
                    ") VALUES(?,?,? ,?,?,'image',?,?,?,?,?,?,?)",
                    (
                        attachment_id,
                        "u",
                        "c",
                        assistant_id,
                        request_id,
                        status_value,
                        media_id,
                        identity_state,
                        int(status_value == "failed"),
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
            conn.execute(
                "INSERT INTO async_jobs("
                "id,user_id,chat_id,capability_request_id,kind,status,cancel_requested,"
                "created_at,updated_at,completed_at,result_json"
                ") VALUES('missing-job','u','c','missing-request','image','completed',0,5,5,5,?)",
                (json.dumps({"mediaId": "missing-media"}),),
            )
            conn.commit()
            conn.close()

            upgrade(path, "head")
            conn = database.connect_sqlite(path)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM chat_attachments WHERE capability_request_id='embedded-request'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,identity_state,retry_available "
                        "FROM chat_attachments WHERE id='recovered-attachment'"
                    ).fetchone()
                ),
                ("completed", "recovered-media", "unconditioned", 0),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM capability_requests WHERE id='recovered-request'").fetchone()[0],
                "completed",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available FROM chat_attachments WHERE id='strict-attachment'"
                    ).fetchone()
                ),
                ("failed", None, 1),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,identity_state,retry_available "
                        "FROM chat_attachments WHERE id='generic-attachment'"
                    ).fetchone()
                ),
                ("completed", "generic-media", "not_applicable", 0),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM capability_requests WHERE id='strict-request'").fetchone()[0],
                "failed",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available FROM chat_attachments WHERE id='strict-raw-attachment'"
                    ).fetchone()
                ),
                ("failed", None, 1),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available,safe_error "
                        "FROM chat_attachments WHERE id='missing-attachment'"
                    ).fetchone()
                )[:3],
                ("failed", None, 1),
            )
            self.assertIn(
                "no longer available",
                conn.execute("SELECT safe_error FROM chat_attachments WHERE id='missing-attachment'").fetchone()[0],
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,error_code FROM capability_requests WHERE id='missing-request'"
                    ).fetchone()
                ),
                ("failed", "artifact_missing"),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM async_jobs WHERE id='missing-job'").fetchone()[0],
                "failed",
            )
            self.assertIn(
                "no longer available",
                conn.execute("SELECT error FROM async_jobs WHERE id='missing-job'").fetchone()[0],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM capability_events "
                    "WHERE capability_request_id='missing-request' AND action='failed'"
                ).fetchone()[0],
                1,
            )
            conn.close()

    def test_startup_recovers_only_policy_allowed_generated_media_inside_managed_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "nice_assistant.db"
            image_dir = base / "images"
            identity_dir = base / "identity_references"
            image_dir.mkdir()
            identity_dir.mkdir()
            recovered_path = image_dir / "recovered.png"
            recovered_path.write_bytes(b"image")
            show_path = image_dir / "show.png"
            show_path.write_bytes(b"show")
            strict_path = image_dir / "strict.png"
            strict_path.write_bytes(b"strict")
            generic_path = image_dir / "generic.png"
            generic_path.write_bytes(b"generic")
            outside_path = identity_dir / "private.png"
            outside_path.write_bytes(b"private")
            os.chmod(outside_path, 0o600)
            outside_mode = stat.S_IMODE(outside_path.stat().st_mode)

            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','hash',1,1)"
            )
            conn.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at) VALUES('c','u','Chat',1,1)")
            conn.executemany(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                [
                    ("a-good", "c", "assistant", "", 1),
                    ("a-bad", "c", "assistant", "", 2),
                    ("a-show", "c", "assistant", "", 3),
                    ("a-strict", "c", "assistant", "", 4),
                    ("a-generic", "c", "assistant", "", 5),
                ],
            )
            for suffix, media_path, assistant_id, conditioning, attempt_status in (
                (
                    "good",
                    recovered_path,
                    "a-good",
                    {"status": "unconditioned", "failure_policy": "block_claim"},
                    "running",
                ),
                (
                    "show",
                    show_path,
                    "a-show",
                    {"status": "ready", "failure_policy": "show_unverified"},
                    "failed",
                ),
                (
                    "strict",
                    strict_path,
                    "a-strict",
                    {"status": "ready", "failure_policy": "block_claim"},
                    "failed",
                ),
                ("generic", generic_path, "a-generic", {}, "passed"),
                ("bad", outside_path, "a-bad", {}, "running"),
            ):
                conn.execute(
                    "INSERT INTO capability_requests("
                    "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,"
                    "idempotency_key,requested_at,started_at"
                    ") VALUES(?,?,?,?,?,'running','explicit',?,?,?)",
                    (
                        f"cap-{suffix}",
                        "u",
                        "c",
                        "media.generate_image",
                        "{}",
                        f"restart:{suffix}",
                        3,
                        3,
                    ),
                )
                conn.execute(
                    "INSERT INTO async_jobs("
                    "id,user_id,chat_id,capability_request_id,kind,status,cancel_requested,"
                    "created_at,started_at,updated_at"
                    ") VALUES(?,?,?,?,?,'running',0,3,3,3)",
                    (f"job-{suffix}", "u", "c", f"cap-{suffix}", "image"),
                )
                conn.execute(
                    "INSERT INTO chat_attachments("
                    "id,user_id,chat_id,assistant_message_id,capability_request_id,kind,status,"
                    "identity_state,retry_available,created_at,updated_at"
                    ") VALUES(?,?,?,?,?,'image','running','not_applicable',0,3,3)",
                    (
                        f"attachment-{suffix}",
                        "u",
                        "c",
                        assistant_id,
                        f"cap-{suffix}",
                    ),
                )
                conn.execute(
                    "INSERT INTO media_execution_plans("
                    "id,user_id,capability_request_id,source,status,kind,operation,requirements_json,"
                    "selected_resources_json,execution_options_json,explanation_json,"
                    "identity_conditioning_json,created_at"
                    ") VALUES(?,?,?,'manual','ready','image','generate','{}','[]','{}','{}',?,3)",
                    (
                        f"plan-{suffix}",
                        "u",
                        f"cap-{suffix}",
                        json.dumps(conditioning),
                    ),
                )
                conn.execute(
                    "INSERT INTO media_generation_attempts("
                    "id,user_id,media_plan_id,attempt_number,operation,status,started_at"
                    ") VALUES(?,?,?,1,'generate',?,3)",
                    (f"attempt-{suffix}", "u", f"plan-{suffix}", attempt_status),
                )
                conn.execute(
                    "INSERT INTO media_files("
                    "id,user_id,chat_id,kind,filename,local_path,generation_plan_id,created_at"
                    ") VALUES(?,?,?,'image',?,?,?,3)",
                    (
                        f"media-{suffix}",
                        "u",
                        "c",
                        media_path.name,
                        str(media_path),
                        f"plan-{suffix}",
                    ),
                )
            conn.commit()
            conn.close()

            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available,identity_state FROM chat_attachments "
                        "WHERE id='attachment-good'"
                    ).fetchone()
                ),
                ("completed", "media-good", 0, "unconditioned"),
            )
            recovered_result = json.loads(
                conn.execute("SELECT result_json FROM capability_requests WHERE id='cap-good'").fetchone()[0]
            )
            self.assertEqual(recovered_result["mediaId"], "media-good")
            self.assertEqual(
                conn.execute("SELECT status FROM async_jobs WHERE id='job-good'").fetchone()[0],
                "completed",
            )
            self.assertEqual(
                conn.execute("SELECT status FROM media_generation_attempts WHERE id='attempt-good'").fetchone()[0],
                "unverified",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,identity_state FROM chat_attachments WHERE id='attachment-show'"
                    ).fetchone()
                ),
                ("completed", "media-show", "unverified"),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,retry_available FROM chat_attachments WHERE id='attachment-strict'"
                    ).fetchone()
                ),
                ("failed", None, 1),
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,media_id,identity_state FROM chat_attachments WHERE id='attachment-generic'"
                    ).fetchone()
                ),
                ("completed", "media-generic", "not_applicable"),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM capability_requests WHERE id='cap-strict'").fetchone()[0],
                "failed",
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT status,retry_available FROM chat_attachments WHERE id='attachment-bad'"
                    ).fetchone()
                ),
                ("failed", 1),
            )
            self.assertEqual(
                conn.execute("SELECT status FROM async_jobs WHERE id='job-bad'").fetchone()[0],
                "failed",
            )
            conn.close()
            self.assertEqual(stat.S_IMODE(outside_path.stat().st_mode), outside_mode)

    def test_new_generated_image_is_host_readable_before_restart(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            chat = running.client.post("/api/v1/chats", json={"memory_mode": "off"}).json()
            started = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "a garden", "chat_id": chat["id"]},
            ).json()
            job = running.wait_job(started["job_id"])
            self.assertEqual(job["status"], "completed")
            media_id = job["result"]["mediaId"]
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                media = uow.repo.media(user_id, media_id)
                media_path = Path(media.local_path)
            if os.name == "nt":
                self.assertTrue(stat.S_IMODE(media_path.stat().st_mode) & stat.S_IRUSR)
            else:
                self.assertEqual(stat.S_IMODE(media_path.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
