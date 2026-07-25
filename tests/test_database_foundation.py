import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from alembic import command
from alembic.config import Config

from app import database
from app.secret_store import SECRET_PREFIX, SecretConfigurationError, SecretStore
from app.typed_settings import load_typed_preferences


class DatabaseFoundationTests(unittest.TestCase):
    def test_backup_is_consistent_with_uncheckpointed_wal_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.db"
            backup = Path(tmp) / "backups" / "source.db"
            conn = database.connect_sqlite(path)
            conn.execute("CREATE TABLE example (value TEXT NOT NULL)")
            conn.commit()
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("INSERT INTO example(value) VALUES('preserved')")
            conn.commit()

            self.assertTrue(database.create_verified_backup(path, backup))
            restored = sqlite3.connect(backup)
            self.assertEqual(restored.execute("SELECT value FROM example").fetchone()[0], "preserved")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            restored.close()
            conn.close()

    def test_fresh_database_uses_versioned_schema_and_connection_pragmas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nice_assistant.db"
            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            recursive_triggers = conn.execute("PRAGMA recursive_triggers").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
            engine = database.build_engine(path)
            try:
                with engine.connect() as connection:
                    engine_recursive_triggers = connection.exec_driver_sql("PRAGMA recursive_triggers").scalar_one()
            finally:
                engine.dispose()

            self.assertEqual(version, "0019_memory_v3_identity_access")
            self.assertIn("setting_values", tables)
            self.assertIn("conversation_turns", tables)
            self.assertIn("conversation_summaries", tables)
            self.assertIn("memory_events", tables)
            self.assertIn("memory_fts", tables)
            self.assertIn("capability_requests", tables)
            self.assertIn("capability_events", tables)
            self.assertIn("media_catalog_resources", tables)
            self.assertIn("media_resource_compatibility", tables)
            self.assertIn("media_execution_plans", tables)
            self.assertIn("task_model_profiles", tables)
            self.assertIn("task_model_runs", tables)
            self.assertIn("identity_validation_settings", tables)
            self.assertIn("persona_visual_identities", tables)
            self.assertIn("persona_identity_references", tables)
            self.assertIn("persona_identity_validations", tables)
            self.assertIn("persona_identity_events", tables)
            self.assertIn("media_generation_attempts", tables)
            self.assertIn("resource_coordination_settings", tables)
            self.assertIn("resource_control_authorizations", tables)
            self.assertIn("resource_coordination_events", tables)
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(recursive_triggers, 1)
            self.assertEqual(engine_recursive_triggers, 1)
            self.assertEqual(journal_mode.lower(), "wal")

    def test_turn_migration_preserves_existing_rows_and_enforces_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0003_enforce_relationships")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at) VALUES('c','u','Chat',1,1)")
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('m','c','user','hello',1)")
            conn.execute(
                "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) VALUES('x','u','c','image','x.png','x.png',1)"
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,chat_id,kind,status,cancel_requested,created_at,updated_at) VALUES('j','u','c','image','completed',0,1,1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(conn.execute("SELECT title FROM chats WHERE id='c'").fetchone()[0], "Chat")
            self.assertEqual(conn.execute("SELECT filename FROM media_files WHERE id='x'").fetchone()[0], "x.png")
            self.assertEqual(conn.execute("SELECT status FROM async_jobs WHERE id='j'").fetchone()[0], "completed")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO async_jobs(id,user_id,kind,status,created_at,updated_at) VALUES('bad','u','chat','mystery',1,1)"
                )
            conn.close()

    def test_resource_coordination_migration_preserves_existing_jobs_and_enforces_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resource-coordination-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0011_persona_identity")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES(?,?,?,?,?)",
                ("owner", "owner", "hash", 1, 1),
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,kind,status,cancel_requested,created_at,updated_at,progress) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("job-before-0012", "owner", "image", "completed", 0, 2, 2, "Completed"),
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(
                conn.execute("SELECT status FROM async_jobs WHERE id='job-before-0012'").fetchone()[0],
                "completed",
            )
            conn.execute(
                "INSERT INTO resource_coordination_settings("
                "id,mode,reserve_vram_mb,max_wait_seconds,poll_interval_seconds,created_at,updated_at"
                ") VALUES(1,'observe',1024,300,2,3,3)"
            )
            conn.execute(
                "INSERT INTO resource_control_authorizations("
                "id,provider,endpoint_fingerprint,exclusive_control,allow_release,authorized_by_user_id,created_at,updated_at"
                ") VALUES('auth','comfyui','fingerprint',1,1,'owner',3,3)"
            )
            conn.execute(
                "INSERT INTO resource_coordination_events("
                "id,job_id,user_id,provider,endpoint_fingerprint,action,outcome,detail_json,created_at"
                ") VALUES('event','job-before-0012','owner','comfyui','fingerprint','admitted','success','{}',3)"
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE resource_coordination_settings SET mode='pretend' WHERE id=1")
            conn.close()

    def test_context_migration_sequences_turns_and_preserves_legacy_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0004_conversation_turns")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute(
                "INSERT INTO chats(id,user_id,memory_mode,title,created_at,updated_at) VALUES('c','u','auto','Chat',1,1)"
            )
            conn.executemany(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                [
                    ("m1", "c", "user", "one", 1),
                    ("m2", "c", "assistant", "reply", 2),
                    ("m3", "c", "user", "two", 3),
                ],
            )
            conn.execute(
                "INSERT INTO conversation_turns(id,user_id,chat_id,user_message_id,assistant_message_id,provider,model,status,created_at) VALUES('t2','u','c','m3',NULL,'ollama','m','failed',3)"
            )
            conn.execute(
                "INSERT INTO conversation_turns(id,user_id,chat_id,user_message_id,assistant_message_id,provider,model,status,created_at) VALUES('t1','u','c','m1','m2','ollama','m','completed',1)"
            )
            conn.execute(
                "INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES('memory','u','chat','c','legacy fact',1)"
            )
            conn.execute(
                "INSERT INTO app_settings(user_id,default_memory_mode,preferences_json) VALUES('u','manual',?)",
                (json.dumps({"memory_auto_save_user_facts": True, "kept": "yes"}),),
            )
            conn.execute(
                "INSERT INTO setting_values(user_id,key,value_type,value_json,updated_at) VALUES('u','memory_auto_save_user_facts','bool','true',1)"
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,chat_id,turn_id,kind,status,cancel_requested,created_at,updated_at) "
                "VALUES('linked-job','u','c','t1','chat','completed',0,1,1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            sequences = conn.execute(
                "SELECT id,sequence_number FROM conversation_turns WHERE chat_id='c' ORDER BY sequence_number"
            ).fetchall()
            chat = conn.execute("SELECT memory_mode,last_turn_sequence FROM chats WHERE id='c'").fetchone()
            settings = conn.execute(
                "SELECT default_memory_mode,preferences_json FROM app_settings WHERE user_id='u'"
            ).fetchone()
            obsolete = conn.execute(
                "SELECT COUNT(*) FROM setting_values WHERE key='memory_auto_save_user_facts'"
            ).fetchone()[0]
            memory_count = conn.execute("SELECT COUNT(*) FROM memories WHERE id='memory'").fetchone()[0]
            linked_turn_id = conn.execute("SELECT turn_id FROM async_jobs WHERE id='linked-job'").fetchone()[0]
            conn.close()
            self.assertEqual([tuple(row) for row in sequences], [("t1", 1), ("t2", 2)])
            self.assertEqual(tuple(chat), ("saved", 2))
            self.assertEqual(settings[0], "saved")
            self.assertEqual(json.loads(settings[1]), {"kept": "yes"})
            self.assertEqual(obsolete, 0)
            self.assertEqual(memory_count, 1)
            self.assertEqual(linked_turn_id, "t1")

    def test_memory_v2_migration_preserves_rows_and_supersedes_only_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v2-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0005_causal_context")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.executemany(
                "INSERT INTO memories(id,user_id,tier,tier_ref_id,content,created_at) VALUES(?,?,?,?,?,?)",
                [
                    ("old", "u", "global", None, "Favorite   color is Blue", 1),
                    ("new", "u", "global", None, "favorite color is blue", 2),
                    ("other", "u", "global", None, "Keeps a garden", 3),
                ],
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            rows = conn.execute(
                "SELECT id,status,source_type,normalized_content,supersedes_id FROM memories ORDER BY id"
            ).fetchall()
            events = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]
            matches = conn.execute("SELECT memory_id FROM memory_fts WHERE memory_fts MATCH 'garden'").fetchall()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memories(id,user_id,tier,content,normalized_content,status,source_type,created_at,updated_at) "
                    "VALUES('duplicate','u','global','FAVORITE COLOR IS BLUE','favorite color is blue','active','manual',4,4)"
                )
            conn.close()
            by_id = {row[0]: tuple(row[1:]) for row in rows}
            self.assertEqual(len(rows), 3)
            self.assertEqual(by_id["old"][0], "superseded")
            self.assertEqual(by_id["new"], ("active", "legacy", "favorite color is blue", "old"))
            self.assertEqual(by_id["other"][0], "active")
            self.assertEqual(events, 3)
            self.assertEqual([row[0] for row in matches], ["other"])

    def test_browser_cutover_migration_rewrites_saved_artifact_links_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "browser-cutover.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0006_memory_v2")
            engine.dispose()

            legacy_url = "/api/images/u_deadbeef.png"
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at) VALUES('c','u','Chat',1,1)")
            conn.executemany(
                "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
                [
                    ("m1", "c", "user", "make it", 1),
                    ("m2", "c", "assistant", f"![Generated image]({legacy_url})", 2),
                ],
            )
            conn.execute(
                "INSERT INTO conversation_summaries("
                "id,user_id,chat_id,sequence_number,through_message_id,provider,model,prompt_version,"
                "source_digest,source_message_count,content,estimated_tokens,created_at"
                ") VALUES('s','u','c',1,'m2','ollama','m','v','digest',2,?,10,2)",
                (f"Previously shared {legacy_url}",),
            )
            conn.execute(
                "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                "VALUES('media','u','c','image','u_deadbeef.png','images/u_deadbeef.png',2)"
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,chat_id,kind,status,cancel_requested,created_at,updated_at,result_json) "
                "VALUES('job','u','c','image','completed',0,2,2,?)",
                (json.dumps({"imageUrl": legacy_url, "text": f"![Generated image]({legacy_url})"}),),
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            message = conn.execute("SELECT text FROM messages WHERE id='m2'").fetchone()[0]
            summary = conn.execute("SELECT content FROM conversation_summaries WHERE id='s'").fetchone()[0]
            result = conn.execute("SELECT result_json FROM async_jobs WHERE id='job'").fetchone()[0]
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("chats", "messages", "media_files", "async_jobs", "conversation_summaries")
            }
            conn.close()
            canonical = "/api/v1/media/media"
            self.assertIn(canonical, message)
            self.assertIn(canonical, summary)
            self.assertIn(canonical, result)
            self.assertNotIn(legacy_url, message + summary + result)
            self.assertEqual(
                counts, {"chats": 1, "messages": 2, "media_files": 1, "async_jobs": 1, "conversation_summaries": 1}
            )

    def test_capability_migration_preserves_jobs_turns_messages_and_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capability-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0007_browser_v1_cutover")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute(
                "INSERT INTO app_settings(user_id,preferences_json) VALUES('u',?)",
                (json.dumps({"image_prompt_generation": True, "kept": "yes"}),),
            )
            conn.execute(
                "INSERT INTO setting_values(user_id,key,value_type,value_json,updated_at) "
                "VALUES('u','image_prompt_generation','bool','true',1)"
            )
            conn.execute(
                "INSERT INTO chats(id,user_id,last_turn_sequence,title,created_at,updated_at) VALUES('c','u',1,'Chat',1,1)"
            )
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('m','c','user','hello',1)")
            conn.execute(
                "INSERT INTO conversation_turns("
                "id,user_id,chat_id,user_message_id,sequence_number,provider,model,status,created_at"
                ") VALUES('t','u','c','m',1,'ollama','model','completed',1)"
            )
            conn.execute(
                "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                "VALUES('media','u','c','image','image.png','image.png',1)"
            )
            conn.execute(
                "INSERT INTO async_jobs("
                "id,user_id,chat_id,turn_id,kind,status,cancel_requested,created_at,updated_at"
                ") VALUES('job','u','c','t','chat','completed',0,1,1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE id='m'").fetchone()[0], "hello")
            self.assertEqual(
                conn.execute("SELECT status FROM conversation_turns WHERE id='t'").fetchone()[0], "completed"
            )
            self.assertEqual(conn.execute("SELECT status FROM async_jobs WHERE id='job'").fetchone()[0], "completed")
            self.assertEqual(
                conn.execute("SELECT filename FROM media_files WHERE id='media'").fetchone()[0], "image.png"
            )
            self.assertEqual(
                json.loads(conn.execute("SELECT preferences_json FROM app_settings WHERE user_id='u'").fetchone()[0]),
                {"kept": "yes"},
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM setting_values WHERE key='image_prompt_generation'").fetchone()[0],
                0,
            )
            conn.execute(
                "INSERT INTO capability_requests("
                "id,user_id,chat_id,turn_id,capability_key,arguments_json,status,permission_mode,idempotency_key,requested_at"
                ") VALUES('cap','u','c','t','media.generate_image','{}','pending_confirmation','confirm','key',1)"
            )
            conn.execute("UPDATE async_jobs SET capability_request_id='cap' WHERE id='job'")
            self.assertEqual(
                conn.execute("SELECT capability_request_id FROM async_jobs WHERE id='job'").fetchone()[0],
                "cap",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO capability_requests("
                    "id,user_id,capability_key,arguments_json,status,permission_mode,idempotency_key,requested_at"
                    ") VALUES('bad','u','media.generate_image','{}','pretend','confirm','bad',1)"
                )
            conn.close()

    def test_task_and_media_catalog_migrations_preserve_data_and_seed_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task-model-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0008_capability_framework")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute(
                "INSERT INTO app_settings(user_id,global_default_model,preferences_json) "
                'VALUES(\'u\',\'legacy-model\',\'{"image_provider":"local","image_local_backend":"comfyui",'
                '"image_local_model":"legacy-image.safetensors","image_local_allow_nsfw":true}\')'
            )
            conn.execute(
                "INSERT INTO chats(id,user_id,last_turn_sequence,title,created_at,updated_at) VALUES('c','u',1,'Chat',1,1)"
            )
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('m','c','user','hello',1)")
            conn.execute(
                "INSERT INTO conversation_turns("
                "id,user_id,chat_id,user_message_id,sequence_number,provider,model,status,created_at"
                ") VALUES('t','u','c','m',1,'ollama','legacy-model','completed',1)"
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,chat_id,turn_id,kind,status,cancel_requested,created_at,updated_at) "
                "VALUES('j','u','c','t','chat','completed',0,1,1)"
            )
            conn.execute(
                "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                "VALUES('media','u','c','image','image.png','image.png',1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            profiles = conn.execute(
                "SELECT role,provider,model FROM task_model_profiles WHERE user_id='u' ORDER BY role"
            ).fetchall()
            self.assertEqual(len(profiles), 4)
            self.assertTrue(all(tuple(row[1:]) == ("ollama", "legacy-model") for row in profiles))
            imported = conn.execute(
                "SELECT provider_key,backend,external_id,content_tags_json FROM media_catalog_resources WHERE user_id='u'"
            ).fetchone()
            self.assertEqual(tuple(imported[:3]), ("local-image", "comfyui", "legacy-image.safetensors"))
            self.assertIn("explicit", imported[3])
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT vram_budget_mb,max_loras,legacy_imported FROM media_catalog_settings WHERE user_id='u'"
                    ).fetchone()
                ),
                (10240, 4, 1),
            )
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE id='m'").fetchone()[0], "hello")
            self.assertEqual(
                conn.execute("SELECT status FROM conversation_turns WHERE id='t'").fetchone()[0], "completed"
            )
            self.assertEqual(conn.execute("SELECT status FROM async_jobs WHERE id='j'").fetchone()[0], "completed")
            self.assertEqual(
                conn.execute("SELECT filename FROM media_files WHERE id='media'").fetchone()[0], "image.png"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO task_model_runs("
                    "id,user_id,role,status,fallback_used,attempts_json,input_tokens_estimated,started_at"
                    ") VALUES('bad','u','title_generation','pretend',0,'[]',1,1)"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO media_catalog_resources("
                    "id,user_id,resource_type,kind,name,provider_key,backend,external_id,created_at,updated_at"
                    ") VALUES('bad-resource','u','pretend','image','Bad','local-image','comfyui','bad',1,1)"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE media_catalog_settings SET max_loras=99 WHERE user_id='u'")
            conn.close()

    def test_media_provider_bootstrap_repairs_late_enablement_without_overwriting_catalogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "late-image-provider.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0014_media_correction_workflows")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute(
                "INSERT INTO app_settings(user_id,preferences_json) "
                'VALUES(\'u\',\'{"image_provider":"local/comfyui","image_local_model":"late.safetensors"}\')'
            )
            conn.execute(
                "INSERT INTO media_catalog_settings(user_id,vram_budget_mb,max_loras,legacy_imported,created_at,updated_at) "
                "VALUES('u',10240,4,1,1,1)"
            )
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u2','curator','h',0,1)"
            )
            conn.execute(
                "INSERT INTO app_settings(user_id,preferences_json) "
                'VALUES(\'u2\',\'{"image_provider":"local/comfyui","image_local_model":"ignored.safetensors"}\')'
            )
            conn.execute(
                "INSERT INTO media_catalog_settings(user_id,vram_budget_mb,max_loras,legacy_imported,created_at,updated_at) "
                "VALUES('u2',10240,4,1,1,1)"
            )
            conn.execute(
                "INSERT INTO media_catalog_resources("
                "id,user_id,resource_type,kind,name,provider_key,backend,external_id,created_at,updated_at"
                ") VALUES('curated','u2','model','image','Curated model','local-image','comfyui',"
                "'curated.safetensors',1,1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            imported = conn.execute(
                "SELECT provider_key,backend,external_id FROM media_catalog_resources WHERE user_id='u'"
            ).fetchall()
            self.assertEqual([tuple(row) for row in imported], [("local-image", "comfyui", "late.safetensors")])
            curated = conn.execute("SELECT name,external_id FROM media_catalog_resources WHERE user_id='u2'").fetchall()
            self.assertEqual([tuple(row) for row in curated], [("Curated model", "curated.safetensors")])
            conn.close()

    def test_identity_fallback_migration_preserves_profiles_and_sets_a_truthful_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity-fallback.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0015_media_provider_bootstrap")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','Avery','{}',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.execute(
                "INSERT INTO persona_visual_identities("
                "id,user_id,persona_id,status,consent_status,appearance_description,acceptance_threshold,"
                "max_generation_attempts,failure_policy,revision,last_validation_sequence,last_event_sequence,"
                "created_at,updated_at"
                ") VALUES('identity','u','p','draft','granted','green eyes',0.78,2,'block_claim',3,0,0,1,1)"
            )
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','w','p','Identity setup',1,1)"
            )
            conn.execute(
                "INSERT INTO capability_requests("
                "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,idempotency_key,requested_at"
                ") VALUES('cap','u','c','media.generate_image','{}','pending_confirmation','confirm','fallback-plan',1)"
            )
            conn.execute(
                "INSERT INTO capability_events("
                "id,user_id,capability_request_id,action,from_status,to_status,detail_json,created_at"
                ") VALUES('event','u','cap','requested',NULL,'pending_confirmation','{}',1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            profile = conn.execute(
                "SELECT appearance_description,revision,conditioning_fallback "
                "FROM persona_visual_identities WHERE id='identity'"
            ).fetchone()
            self.assertEqual(tuple(profile), ("green eyes", 3, "allow_unconditioned"))
            self.assertEqual(
                conn.execute("SELECT action FROM capability_events WHERE id='event'").fetchone()[0],
                "requested",
            )
            conn.execute(
                "INSERT INTO capability_events("
                "id,user_id,capability_request_id,action,from_status,to_status,detail_json,created_at"
                ") VALUES('replan-event','u','cap','replanned','pending_confirmation','pending_confirmation','{}',2)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE persona_visual_identities SET conditioning_fallback='pretend' WHERE id='identity'")
            conn.close()

    def test_persona_identity_migration_preserves_existing_persona_media_and_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0010_media_catalog")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','Avery','{}',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.execute(
                "INSERT INTO media_files(id,user_id,kind,filename,local_path,created_at) "
                "VALUES('m','u','image','image.png','image.png',1)"
            )
            conn.execute(
                "INSERT INTO async_jobs(id,user_id,kind,status,cancel_requested,created_at,updated_at) "
                "VALUES('j','u','image','completed',0,1,1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            self.assertEqual(conn.execute("SELECT name FROM personas WHERE id='p'").fetchone()[0], "Avery")
            self.assertEqual(conn.execute("SELECT filename FROM media_files WHERE id='m'").fetchone()[0], "image.png")
            self.assertEqual(conn.execute("SELECT status FROM async_jobs WHERE id='j'").fetchone()[0], "completed")
            conn.execute(
                "INSERT INTO persona_visual_identities("
                "id,user_id,persona_id,status,consent_status,acceptance_threshold,max_generation_attempts,"
                "failure_policy,revision,last_validation_sequence,last_event_sequence,created_at,updated_at"
                ") VALUES('i','u','p','draft','not_granted',0.78,2,'block_claim',1,0,0,1,1)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO persona_identity_validations("
                    "id,user_id,identity_id,persona_id,candidate_media_id,sequence_number,created_order,provider,status,failure_policy,threshold,created_at"
                    ") VALUES('bad','u','i','p','m',1,1,'compreface','pretend','block_claim',0.78,1)"
                )
            conn.close()

    def test_identity_generation_migration_preserves_plans_and_links_generated_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity-generation-upgrade.db"
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
            config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0012_resource_coordination")
            engine.dispose()
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','Avery','{}',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.execute(
                "INSERT INTO chats(id,user_id,workspace_id,persona_id,title,created_at,updated_at) "
                "VALUES('c','u','w','p','Chat',1,1)"
            )
            conn.execute(
                "INSERT INTO capability_requests("
                "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,idempotency_key,requested_at"
                ") VALUES('cap','u','c','media.generate_image','{}','completed','confirm','identity-plan',1)"
            )
            conn.execute(
                "INSERT INTO media_execution_plans("
                "id,user_id,capability_request_id,source,status,kind,operation,requirements_json,"
                "selected_resources_json,execution_options_json,explanation_json,created_at"
                ") VALUES('plan','u','cap','coordinator','ready','image','generate','{}','[]','{}','{}',1)"
            )
            conn.execute(
                "INSERT INTO media_files(id,user_id,chat_id,kind,filename,local_path,created_at) "
                "VALUES('old-media','u','c','image','old.png','old.png',1)"
            )
            conn.commit()
            conn.close()

            engine = database.build_engine(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            engine.dispose()
            conn = database.connect_sqlite(path)
            old_plan = conn.execute(
                "SELECT persona_id,identity_conditioning_json FROM media_execution_plans WHERE id='plan'"
            ).fetchone()
            self.assertEqual(tuple(old_plan), (None, "{}"))
            self.assertEqual(
                tuple(
                    conn.execute("SELECT filename,generation_plan_id FROM media_files WHERE id='old-media'").fetchone()
                ),
                ("old.png", None),
            )
            conn.execute(
                "UPDATE media_execution_plans SET persona_id='p',identity_profile_id='identity',"
                "identity_profile_revision=3,identity_reference_id='reference',identity_reference_sha256='abc',"
                "identity_conditioning_json=? WHERE id='plan'",
                ('{"required":true}',),
            )
            conn.execute(
                "INSERT INTO media_files("
                "id,user_id,chat_id,kind,filename,local_path,generation_plan_id,created_at"
                ") VALUES('new-media','u','c','image','new.png','new.png','plan',2)"
            )
            self.assertEqual(
                conn.execute("SELECT generation_plan_id FROM media_files WHERE id='new-media'").fetchone()[0],
                "plan",
            )
            conn.execute(
                "INSERT INTO media_generation_attempts("
                "id,user_id,media_plan_id,attempt_number,operation,status,media_id,started_at,completed_at"
                ") VALUES('attempt','u','plan',1,'generate','passed','new-media',2,3)"
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT operation,status,media_id FROM media_generation_attempts WHERE id='attempt'"
                    ).fetchone()
                ),
                ("generate", "passed", "new-media"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO media_generation_attempts("
                    "id,user_id,media_plan_id,attempt_number,operation,status,started_at"
                    ") VALUES('bad-attempt','u','plan',2,'pretend','running',2)"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO media_files("
                    "id,user_id,kind,filename,local_path,generation_plan_id,created_at"
                    ") VALUES('bad-media','u','image','bad.png','bad.png','missing-plan',3)"
                )
            conn.close()

    def test_restart_recovery_fails_linked_unfinished_job_and_turn_without_assistant_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "restart.db"
            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            conn.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u','owner','h',1,1)")
            conn.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at) VALUES('c','u','Chat',1,1)")
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('m','c','user','hello',1)")
            conn.execute("INSERT INTO messages(id,chat_id,role,text,created_at) VALUES('a','c','assistant','',1)")
            conn.execute(
                """
                INSERT INTO conversation_turns(
                    id,user_id,chat_id,user_message_id,sequence_number,provider,model,status,created_at,started_at
                ) VALUES('t','u','c','m',1,'ollama','model','running',1,1)
                """
            )
            conn.execute(
                """
                INSERT INTO async_jobs(
                    id,user_id,chat_id,turn_id,kind,status,cancel_requested,created_at,started_at,updated_at
                ) VALUES('j','u','c','t','chat','running',0,1,1,1)
                """
            )
            conn.execute(
                "INSERT INTO capability_requests("
                "id,user_id,chat_id,capability_key,arguments_json,status,permission_mode,idempotency_key,requested_at,started_at"
                ") VALUES('cap','u','c','media.generate_image','{}','running','explicit','restart-capability',1,1)"
            )
            conn.execute(
                "INSERT INTO async_jobs("
                "id,user_id,chat_id,capability_request_id,kind,status,cancel_requested,created_at,started_at,updated_at"
                ") VALUES('cap-job','u','c','cap','image','running',0,1,1,1)"
            )
            conn.execute(
                "INSERT INTO chat_attachments("
                "id,user_id,chat_id,assistant_message_id,capability_request_id,kind,status,identity_state,"
                "retry_available,created_at,updated_at"
                ") VALUES('attachment','u','c','a','cap','image','running','not_applicable',0,1,1)"
            )
            conn.execute(
                "INSERT INTO media_execution_plans("
                "id,user_id,capability_request_id,source,status,kind,operation,requirements_json,"
                "selected_resources_json,execution_options_json,explanation_json,created_at"
                ") VALUES('restart-plan','u','cap','manual','ready','image','generate','{}','[]','{}','{}',1)"
            )
            conn.execute(
                "INSERT INTO media_generation_attempts("
                "id,user_id,media_plan_id,attempt_number,operation,status,started_at"
                ") VALUES('restart-attempt','u','restart-plan',1,'generate','running',1)"
            )
            conn.execute(
                "INSERT INTO task_model_runs("
                "id,user_id,role,requested_provider,requested_model,status,fallback_used,attempts_json,"
                "input_tokens_estimated,started_at"
                ") VALUES('task-run','u','title_generation','ollama','model','running',0,'[]',10,1)"
            )
            conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('w','u','World',1)")
            conn.execute(
                "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) VALUES('p','w','Avery','{}',1)"
            )
            conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('p','w')")
            conn.execute(
                "INSERT INTO media_files(id,user_id,kind,filename,local_path,created_at) "
                "VALUES('identity-media','u','image','identity.png','identity.png',1)"
            )
            conn.execute(
                "INSERT INTO persona_visual_identities("
                "id,user_id,persona_id,status,consent_status,acceptance_threshold,max_generation_attempts,"
                "failure_policy,revision,last_validation_sequence,last_event_sequence,created_at,updated_at"
                ") VALUES('identity','u','p','active','granted',0.78,2,'block_claim',1,1,0,1,1)"
            )
            conn.execute(
                "INSERT INTO persona_identity_validations("
                "id,user_id,identity_id,persona_id,candidate_media_id,sequence_number,created_order,provider,status,failure_policy,threshold,created_at,started_at"
                ") VALUES('validation','u','identity','p','identity-media',1,1,'compreface','running','block_claim',0.78,1,1)"
            )
            conn.commit()
            conn.close()

            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            job = conn.execute("SELECT status,error,completed_at FROM async_jobs WHERE id='j'").fetchone()
            turn = conn.execute(
                "SELECT status,error_code,error_message,completed_at,assistant_message_id FROM conversation_turns WHERE id='t'"
            ).fetchone()
            assistant_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id='c' AND role='assistant'"
            ).fetchone()[0]
            capability = conn.execute(
                "SELECT status,error_code,error_message,completed_at FROM capability_requests WHERE id='cap'"
            ).fetchone()
            task_run = conn.execute(
                "SELECT status,error_code,error_message,completed_at FROM task_model_runs WHERE id='task-run'"
            ).fetchone()
            identity_validation = conn.execute(
                "SELECT status,error_code,error_message,completed_at FROM persona_identity_validations WHERE id='validation'"
            ).fetchone()
            media_attempt = conn.execute(
                "SELECT status,error_code,error_message,completed_at FROM media_generation_attempts "
                "WHERE id='restart-attempt'"
            ).fetchone()
            attachment = conn.execute(
                "SELECT status,safe_error,retry_available,completed_at FROM chat_attachments WHERE id='attachment'"
            ).fetchone()
            conn.close()
            self.assertEqual(job[0:2], ("failed", "interrupted by server restart"))
            self.assertIsNotNone(job[2])
            self.assertEqual(turn[0:3], ("failed", "interrupted", "interrupted by server restart"))
            self.assertIsNotNone(turn[3])
            self.assertIsNone(turn[4])
            self.assertEqual(assistant_count, 1)
            self.assertEqual(capability[0:3], ("failed", "interrupted", "interrupted by server restart"))
            self.assertIsNotNone(capability[3])
            self.assertEqual(task_run[0:3], ("failed", "interrupted", "interrupted by server restart"))
            self.assertIsNotNone(task_run[3])
            self.assertEqual(
                identity_validation[0:3],
                ("error", "interrupted", "interrupted by server restart"),
            )
            self.assertIsNotNone(identity_validation[3])
            self.assertEqual(
                media_attempt[0:3],
                ("error", "interrupted", "interrupted by server restart"),
            )
            self.assertIsNotNone(media_attempt[3])
            self.assertEqual(
                attachment[0:3],
                ("failed", "Image generation was interrupted by a restart.", 1),
            )
            self.assertIsNotNone(attachment[3])

    def test_pre_alembic_preferences_are_migrated_to_typed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE app_settings (user_id TEXT PRIMARY KEY, openai_api_key TEXT, preferences_json TEXT);
                INSERT INTO users(id,username,password_hash,created_at) VALUES('u1','owner','hash',1);
                """
            )
            conn.execute(
                "INSERT INTO app_settings(user_id,preferences_json) VALUES(?,?)",
                ("u1", json.dumps({"speech_enabled": True, "voice_speed": 1.25, "label": "Guide"})),
            )
            conn.commit()
            conn.close()

            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            values = load_typed_preferences(conn, "u1", "{}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(app_settings)")}
            conn.close()

            self.assertEqual(
                values,
                {
                    "label": "Guide",
                    "speech_enabled": True,
                    "voice_speed": 1.25,
                    "chat_blur_images": False,
                },
            )
            self.assertIn("openai_api_key_encrypted", columns)

            conn = database.connect_sqlite(path)
            foreign_keys = conn.execute("PRAGMA foreign_key_list(app_settings)").fetchall()
            conn.close()
            self.assertTrue(any(row[2] == "users" and row[3] == "user_id" for row in foreign_keys))

    def test_legacy_secret_is_encrypted_when_master_key_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-secret.db"
            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u1','owner','hash',1,1)"
            )
            conn.execute("INSERT INTO app_settings(user_id,openai_api_key) VALUES('u1','sk-legacy-secret')")
            conn.commit()
            conn.close()

            store = SecretStore("deployment-test-key")
            with mock.patch("app.database.SECRET_STORE", store):
                database.initialize_database(path, 1800)

            conn = database.connect_sqlite(path)
            row = conn.execute(
                "SELECT openai_api_key,openai_api_key_encrypted FROM app_settings WHERE user_id='u1'"
            ).fetchone()
            conn.close()
            self.assertIsNone(row[0])
            self.assertTrue(row[1].startswith(SECRET_PREFIX))
            self.assertEqual(store.decrypt(row[1]), "sk-legacy-secret")

    def test_database_with_plaintext_secret_refuses_startup_without_master_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-secret.db"
            database.initialize_database(path, 1800)
            conn = database.connect_sqlite(path)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('u1','owner','hash',1,1)"
            )
            conn.execute("INSERT INTO app_settings(user_id,openai_api_key) VALUES('u1','sk-legacy-secret')")
            conn.commit()
            conn.close()

            with mock.patch("app.database.SECRET_STORE", SecretStore("")):
                with self.assertRaisesRegex(RuntimeError, "NICE_ASSISTANT_MASTER_KEY"):
                    database.initialize_database(path, 1800)


class SecretStoreTests(unittest.TestCase):
    def test_secret_round_trip_and_wrong_key_failure(self):
        encrypted = SecretStore("one-key").encrypt("sk-private")
        self.assertEqual(SecretStore("one-key").decrypt(encrypted), "sk-private")
        with self.assertRaises(SecretConfigurationError):
            SecretStore("different-key").decrypt(encrypted)

    def test_secret_write_requires_master_key(self):
        with self.assertRaises(SecretConfigurationError):
            SecretStore("").encrypt("sk-private")


class MemoryV3PersistenceMigrationTests(unittest.TestCase):
    @staticmethod
    def _config(path: Path) -> Config:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        config.set_main_option("sqlalchemy.url", database.sqlite_url(path))
        return config

    def _legacy_database(self, path: Path) -> Config:
        config = self._config(path)
        engine = database.build_engine(path)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018_human_image_delivery")
        engine.dispose()

        conn = database.connect_sqlite(path)
        conn.execute(
            "INSERT INTO users(id,username,password_hash,is_admin,created_at) VALUES('owner','owner','hash',1,10)"
        )
        conn.execute("INSERT INTO workspaces(id,user_id,name,created_at) VALUES('work','owner','Studio',11)")
        conn.execute(
            "INSERT INTO personas(id,workspace_id,name,traits_json,created_at) "
            "VALUES('persona','work','Advisor','{}',12)"
        )
        conn.execute("INSERT INTO persona_workspace_links(persona_id,workspace_id) VALUES('persona','work')")
        conn.execute(
            "INSERT INTO chats("
            "id,user_id,workspace_id,persona_id,memory_mode,title,last_turn_sequence,created_at,updated_at"
            ") VALUES('chat','owner','work','persona','saved','Legacy chat',1,13,18)"
        )
        conn.executemany(
            "INSERT INTO messages(id,chat_id,role,text,created_at) VALUES(?,?,?,?,?)",
            [
                ("user-message", "chat", "user", "I prefer concise replies.", 14),
                ("assistant-message", "chat", "assistant", "Understood.", 15),
            ],
        )
        conn.execute(
            "INSERT INTO conversation_turns("
            "id,user_id,chat_id,user_message_id,assistant_message_id,sequence_number,"
            "provider,model,status,created_at,completed_at"
            ") VALUES('turn','owner','chat','user-message','assistant-message',1,"
            "'ollama','model','completed',14,15)"
        )
        conn.executemany(
            "INSERT INTO memories("
            "id,user_id,tier,tier_ref_id,content,normalized_content,status,source_type,"
            "source_message_id,source_turn_id,confidence,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "conversation-memory",
                    "owner",
                    "workspace",
                    "work",
                    "The user prefers concise replies.",
                    "the user prefers concise replies.",
                    "pending",
                    "conversation",
                    "user-message",
                    "turn",
                    0.9,
                    16,
                    16,
                ),
                (
                    "global-memory",
                    "owner",
                    "global",
                    None,
                    "Do not turn this into a profile value.",
                    "do not turn this into a profile value.",
                    "active",
                    "manual",
                    None,
                    None,
                    None,
                    17,
                    17,
                ),
            ],
        )
        conn.commit()
        conn.close()
        return config

    @staticmethod
    def _migrate(path: Path, config: Config, revision: str) -> None:
        engine = database.build_engine(path)
        try:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                if revision == "0018_human_image_delivery":
                    command.downgrade(config, revision)
                else:
                    command.upgrade(config, revision)
        finally:
            engine.dispose()

    def test_v3_foundation_preserves_and_quarantines_every_legacy_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v3-upgrade.db"
            config = self._legacy_database(path)
            before = database.connect_sqlite(path)
            chats_before = [tuple(row) for row in before.execute("SELECT * FROM chats ORDER BY id")]
            memories_before = [tuple(row) for row in before.execute("SELECT * FROM memories ORDER BY id")]
            before.close()

            self._migrate(path, config, "0019_memory_v3_identity_access")

            conn = database.connect_sqlite(path)
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM chats ORDER BY id")], chats_before)
            self.assertEqual(
                [tuple(row) for row in conn.execute("SELECT * FROM memories ORDER BY id")],
                memories_before,
            )
            human = conn.execute("SELECT id,user_id,created_at,updated_at FROM human_principals").fetchone()
            self.assertEqual(tuple(human[1:]), ("owner", 10, 10))
            profile = conn.execute(
                "SELECT name,name_pronunciation,pronouns,time_zone,locale,preferred_language,"
                "measurement_units,communication_needs,accessibility_needs,revision "
                "FROM owner_profiles WHERE human_id=?",
                (human["id"],),
            ).fetchone()
            self.assertEqual(tuple(profile), (None, None, None, None, None, None, None, None, None, 0))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM owner_profile_events").fetchone()[0], 0)

            binding = conn.execute("SELECT * FROM chat_bindings WHERE chat_id='chat'").fetchone()
            self.assertEqual(binding["human_id"], human["id"])
            self.assertEqual(binding["binding_status"], "legacy_unresolved")
            self.assertEqual(binding["context_kind"], "legacy_unresolved")
            self.assertEqual(binding["persona_id"], "persona")
            self.assertEqual(binding["workspace_id"], "work")
            self.assertEqual(binding["persona_name_snapshot"], "Advisor")
            self.assertEqual(binding["workspace_name_snapshot"], "Studio")

            records = conn.execute(
                "SELECT memory_id,lineage,access_state,memory_type,validity_status,valid_until,"
                "stateful_status,last_confirmed_at FROM memory_records ORDER BY memory_id"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in records],
                [
                    (
                        "conversation-memory",
                        "legacy_migrated",
                        "legacy_quarantined",
                        "legacy_unknown",
                        "legacy_unknown",
                        None,
                        None,
                        None,
                    ),
                    (
                        "global-memory",
                        "legacy_migrated",
                        "legacy_quarantined",
                        "legacy_unknown",
                        "legacy_unknown",
                        None,
                        None,
                        None,
                    ),
                ],
            )
            origins = {row["memory_id"]: row for row in conn.execute("SELECT * FROM memory_origins ORDER BY memory_id")}
            self.assertEqual(origins["conversation-memory"]["provenance_status"], "legacy_unresolved")
            self.assertEqual(origins["conversation-memory"]["source_chat_id"], "chat")
            self.assertEqual(origins["conversation-memory"]["source_persona_id"], "persona")
            self.assertEqual(origins["conversation-memory"]["source_workspace_id"], "work")
            self.assertEqual(origins["conversation-memory"]["evidence_json"], "[]")
            self.assertEqual(origins["global-memory"]["provenance_status"], "legacy_unresolved")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_grants").fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memory_grants("
                    "id,memory_id,human_id,grant_type,persona_id,workspace_id,"
                    "grant_source,granted_by_human_id,granted_at"
                    ") VALUES('legacy-grant','conversation-memory',?,'persona',"
                    "'persona',NULL,'owner',?,30)",
                    (human["id"], human["id"]),
                )
            conn.rollback()

            conn.execute("DELETE FROM personas WHERE id='persona'")
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT persona_id FROM chat_bindings WHERE chat_id='chat'").fetchone()[0],
                "persona",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT source_persona_id FROM memory_origins WHERE memory_id='conversation-memory'"
                ).fetchone()[0],
                "persona",
            )
            conn.close()

    def test_v3_constraints_make_bindings_origins_validity_and_grants_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v3-constraints.db"
            config = self._legacy_database(path)
            self._migrate(path, config, "0019_memory_v3_identity_access")
            conn = database.connect_sqlite(path)
            human_id = conn.execute("SELECT id FROM human_principals WHERE user_id='owner'").fetchone()[0]

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE chat_bindings SET persona_id='different' WHERE chat_id='chat'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE memory_origins SET source_kind='manual' WHERE memory_id='conversation-memory'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM chat_bindings WHERE chat_id='chat'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM memory_origins WHERE memory_id='conversation-memory'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT OR REPLACE INTO chat_bindings("
                    "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,created_at"
                    ") VALUES('chat',?,'replacement','personal',NULL,'active',99)",
                    (human_id,),
                )
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT OR REPLACE INTO memory_origins("
                    "memory_id,human_id,source_kind,evidence_json,provenance_status,created_at"
                    ") VALUES('conversation-memory',?,'owner_explicit','{}','resolved',99)",
                    (human_id,),
                )
            conn.rollback()
            self.assertEqual(
                conn.execute("SELECT persona_id FROM chat_bindings WHERE chat_id='chat'").fetchone()[0],
                "persona",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT provenance_status FROM memory_origins WHERE memory_id='conversation-memory'"
                ).fetchone()[0],
                "legacy_unresolved",
            )

            conn.execute(
                "INSERT INTO chats(id,user_id,title,memory_mode,created_at,updated_at) "
                "VALUES('new-chat','owner','New','saved',20,20)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO chat_bindings("
                    "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,created_at"
                    ") VALUES('new-chat',?,NULL,'personal',NULL,'active',20)",
                    (human_id,),
                )
            conn.execute(
                "INSERT INTO chat_bindings("
                "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,created_at"
                ") VALUES('new-chat',?,'future-persona','personal',NULL,'active',20)",
                (human_id,),
            )

            conn.execute(
                "INSERT INTO memories("
                "id,user_id,tier,content,normalized_content,status,source_type,created_at,updated_at"
                ") VALUES('new-memory','owner','persona','Current fact','current fact','active','manual',21,21)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memory_records("
                    "memory_id,human_id,access_state,memory_type,validity_status,valid_until,"
                    "stateful_status,last_confirmed_at,created_at,updated_at"
                    ") VALUES('new-memory',?,'grants','temporal','current',NULL,NULL,21,21,21)",
                    (human_id,),
                )
            conn.execute(
                "INSERT INTO memory_records("
                "memory_id,human_id,access_state,memory_type,validity_status,valid_until,"
                "stateful_status,last_confirmed_at,created_at,updated_at"
                ") VALUES('new-memory',?,'grants','temporal','current',30,NULL,21,21,21)",
                (human_id,),
            )
            conn.execute(
                "INSERT INTO memory_origins("
                "memory_id,human_id,source_kind,evidence_json,provenance_status,created_at"
                ") VALUES('new-memory',?,'owner_explicit','[]','resolved',21)",
                (human_id,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memory_grants("
                    "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                    "granted_by_human_id,granted_at"
                    ") VALUES('bad','new-memory',?,'workspace',NULL,'work',"
                    "'automatic_source_persona',?,22)",
                    (human_id, human_id),
                )
            conn.execute(
                "INSERT INTO memory_grants("
                "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                "granted_by_human_id,granted_at"
                ") VALUES('grant-1','new-memory',?,'persona','future-persona',NULL,"
                "'owner',?,22)",
                (human_id, human_id),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memory_grants("
                    "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                    "granted_by_human_id,granted_at"
                    ") VALUES('grant-2','new-memory',?,'persona','future-persona',NULL,"
                    "'owner',?,23)",
                    (human_id, human_id),
                )
            conn.rollback()
            conn.execute(
                "UPDATE memory_grants SET revoked_by_human_id=?,revoked_at=24 WHERE id='grant-1'",
                (human_id,),
            )
            conn.execute(
                "INSERT INTO memory_grants("
                "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                "granted_by_human_id,granted_at"
                ") VALUES('grant-2','new-memory',?,'persona','future-persona',NULL,"
                "'owner',?,25)",
                (human_id, human_id),
            )
            conn.commit()
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_grants WHERE memory_id='new-memory' AND persona_id='future-persona'"
                ).fetchone()[0],
                2,
            )
            conn.close()

    def test_v3_human_ownership_guards_reject_cross_owner_bindings_and_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v3-owner-links.db"
            config = self._legacy_database(path)
            self._migrate(path, config, "0019_memory_v3_identity_access")
            conn = database.connect_sqlite(path)
            human_id = conn.execute("SELECT id FROM human_principals WHERE user_id='owner'").fetchone()[0]
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) "
                "VALUES('other-owner','other-owner','hash',0,30)"
            )
            conn.execute(
                "INSERT INTO human_principals(id,user_id,created_at,updated_at) "
                "VALUES('other-human','other-owner',30,30)"
            )
            conn.execute(
                "INSERT INTO chats(id,user_id,title,memory_mode,created_at,updated_at) "
                "VALUES('owner-chat','owner','Owner chat','saved',31,31)"
            )
            conn.execute(
                "INSERT INTO memories("
                "id,user_id,tier,tier_ref_id,content,normalized_content,status,source_type,created_at,updated_at"
                ") VALUES('owner-memory','owner','persona','future-persona','Owner fact','owner fact',"
                "'active','manual',31,31)"
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO chat_bindings("
                    "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,created_at"
                    ") VALUES('owner-chat','other-human','future-persona','personal',NULL,'active',31)"
                )
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO memory_records("
                    "memory_id,human_id,access_state,memory_type,validity_status,last_confirmed_at,"
                    "created_at,updated_at"
                    ") VALUES('owner-memory','other-human','grants','durable','current',31,31,31)"
                )
            conn.rollback()

            conn.execute(
                "INSERT INTO chat_bindings("
                "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,created_at"
                ") VALUES('owner-chat',?,'future-persona','personal',NULL,'active',31)",
                (human_id,),
            )
            conn.execute(
                "INSERT INTO memory_records("
                "memory_id,human_id,access_state,memory_type,validity_status,last_confirmed_at,"
                "created_at,updated_at"
                ") VALUES('owner-memory',?,'grants','durable','current',31,31,31)",
                (human_id,),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE memory_records SET human_id='other-human' WHERE memory_id='owner-memory'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE memory_records SET lineage='legacy_migrated' WHERE memory_id='owner-memory'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE memory_records SET access_state='legacy_quarantined',"
                    "memory_type='legacy_unknown',validity_status='legacy_unknown',"
                    "last_confirmed_at=NULL WHERE memory_id='owner-memory'"
                )
            conn.rollback()
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT human_id,lineage,access_state FROM memory_records WHERE memory_id='owner-memory'"
                    ).fetchone()
                ),
                (human_id, "native_v3", "grants"),
            )
            conn.close()

    def test_v3_grant_ledger_is_append_only_owner_consistent_and_parent_cascaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v3-grant-ledger.db"
            config = self._legacy_database(path)
            self._migrate(path, config, "0019_memory_v3_identity_access")
            conn = database.connect_sqlite(path)
            human_id = conn.execute("SELECT id FROM human_principals WHERE user_id='owner'").fetchone()[0]

            def add_memory(memory_id: str, user_id: str, owner_human_id: str, stamp: int) -> None:
                conn.execute(
                    "INSERT INTO memories("
                    "id,user_id,tier,tier_ref_id,content,normalized_content,status,source_type,created_at,updated_at"
                    ") VALUES(?,?,?,'future-persona',?,?,?,'manual',?,?)",
                    (
                        memory_id,
                        user_id,
                        "persona",
                        f"Fact {memory_id}",
                        f"fact {memory_id}",
                        "active",
                        stamp,
                        stamp,
                    ),
                )
                conn.execute(
                    "INSERT INTO memory_records("
                    "memory_id,human_id,access_state,memory_type,validity_status,last_confirmed_at,"
                    "created_at,updated_at"
                    ") VALUES(?,?,'grants','durable','current',?,?,?)",
                    (memory_id, owner_human_id, stamp, stamp, stamp),
                )

            add_memory("grant-memory", "owner", human_id, 30)
            add_memory("same-owner-memory", "owner", human_id, 31)
            conn.execute(
                "INSERT INTO users(id,username,password_hash,is_admin,created_at) "
                "VALUES('other-owner','other-owner','hash',0,32)"
            )
            conn.execute(
                "INSERT INTO human_principals(id,user_id,created_at,updated_at) "
                "VALUES('other-human','other-owner',32,32)"
            )
            add_memory("other-owner-memory", "other-owner", "other-human", 32)
            conn.execute(
                "INSERT INTO memory_grants("
                "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                "granted_by_human_id,granted_at"
                ") VALUES('grant-ledger','grant-memory',?,'persona','future-persona',NULL,'owner',?,33)",
                (human_id, human_id),
            )
            conn.execute(
                "INSERT INTO memory_grant_events("
                "id,memory_id,grant_id,human_id,action,grant_type,target_id,created_at"
                ") VALUES('grant-event','grant-memory','grant-ledger',?,'granted','persona',"
                "'future-persona',33)",
                (human_id,),
            )
            conn.commit()

            forbidden_grant_updates = (
                "UPDATE memory_grants SET persona_id='different-persona' WHERE id='grant-ledger'",
                "UPDATE memory_grants SET grant_source='automatic_source_persona' WHERE id='grant-ledger'",
                "UPDATE memory_grants SET granted_at=34 WHERE id='grant-ledger'",
            )
            for statement in forbidden_grant_updates:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
                conn.rollback()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE memory_grant_events SET target_id='changed' WHERE id='grant-event'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM memory_grant_events WHERE id='grant-event'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM memory_grants WHERE id='grant-ledger'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT OR REPLACE INTO memory_grants("
                    "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                    "granted_by_human_id,granted_at"
                    ") VALUES('grant-ledger','grant-memory',?,'persona','replacement-persona',"
                    "NULL,'owner',?,34)",
                    (human_id, human_id),
                )
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT OR REPLACE INTO memory_grant_events("
                    "id,memory_id,grant_id,human_id,action,grant_type,target_id,created_at"
                    ") VALUES('grant-event','grant-memory','grant-ledger',?,'granted','persona',"
                    "'replacement-persona',34)",
                    (human_id,),
                )
            conn.rollback()

            mismatched_events = (
                (
                    "cross-memory-event",
                    "same-owner-memory",
                    human_id,
                ),
                (
                    "cross-owner-event",
                    "other-owner-memory",
                    "other-human",
                ),
            )
            for event_id, memory_id, event_human_id in mismatched_events:
                with self.subTest(event_id=event_id), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO memory_grant_events("
                        "id,memory_id,grant_id,human_id,action,grant_type,target_id,created_at"
                        ") VALUES(?,?,'grant-ledger',?,'granted','persona','future-persona',34)",
                        (event_id, memory_id, event_human_id),
                    )
                conn.rollback()

            conn.execute(
                "UPDATE memory_grants SET revoked_by_human_id=?,revoked_at=35 WHERE id='grant-ledger'",
                (human_id,),
            )
            conn.execute(
                "INSERT INTO memory_grant_events("
                "id,memory_id,grant_id,human_id,action,grant_type,target_id,created_at"
                ") VALUES('revoke-event','grant-memory','grant-ledger',?,'revoked','persona',"
                "'future-persona',35)",
                (human_id,),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE memory_grants SET revoked_by_human_id=NULL,revoked_at=NULL WHERE id='grant-ledger'"
                )
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE memory_grants SET revoked_at=36 WHERE id='grant-ledger'")
            conn.rollback()

            conn.execute("DELETE FROM memories WHERE id='grant-memory'")
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM memory_records WHERE memory_id='grant-memory'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM memory_grants WHERE memory_id='grant-memory'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM memory_grant_events WHERE memory_id='grant-memory'").fetchone()[0],
                0,
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            conn.close()

    def test_v3_foundation_downgrade_refuses_access_broadening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory-v3-downgrade.db"
            config = self._legacy_database(path)
            self._migrate(path, config, "0019_memory_v3_identity_access")
            with closing(database.connect_sqlite(path)) as conn:
                human_id = conn.execute("SELECT id FROM human_principals WHERE user_id='owner'").fetchone()[0]
                conn.execute(
                    "UPDATE memory_records SET access_state='grants',"
                    "memory_type='durable',validity_status='current',"
                    "last_confirmed_at=20,updated_at=20 "
                    "WHERE memory_id='conversation-memory'"
                )
                conn.execute(
                    "INSERT INTO memory_grants("
                    "id,memory_id,human_id,grant_type,persona_id,workspace_id,grant_source,"
                    "granted_by_human_id,granted_at,revoked_by_human_id,revoked_at"
                    ") VALUES('revoked-grant','conversation-memory',?,'persona','persona',NULL,"
                    "'owner',?,20,?,21)",
                    (human_id, human_id, human_id),
                )
                conn.commit()

            with self.assertRaisesRegex(
                RuntimeError,
                "Memory v3 in-place downgrade is not supported",
            ):
                self._migrate(path, config, "0018_human_image_delivery")

            with closing(database.connect_sqlite(path)) as conn:
                self.assertEqual(
                    conn.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                    "0019_memory_v3_identity_access",
                )
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
                for table in (
                    "human_principals",
                    "owner_profiles",
                    "owner_profile_events",
                    "chat_bindings",
                    "memory_records",
                    "memory_origins",
                    "memory_grants",
                    "memory_grant_events",
                ):
                    self.assertIn(table, tables)
                for trigger in (
                    "chat_bindings_identity_immutable",
                    "chat_bindings_delete_guard",
                    "memory_grants_one_way_revocation",
                    "memory_grants_access_state_guard",
                    "memory_grants_delete_guard",
                    "memory_grant_events_immutable",
                    "memory_grant_events_delete_guard",
                    "chat_bindings_owner_guard",
                    "memory_records_owner_guard",
                    "memory_records_identity_immutable",
                    "memory_origins_immutable",
                    "memory_origins_delete_guard",
                ):
                    self.assertIn(trigger, triggers)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    tuple(conn.execute("SELECT persona_id,workspace_id,title FROM chats WHERE id='chat'").fetchone()),
                    ("persona", "work", "Legacy chat"),
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT access_state FROM memory_records WHERE memory_id='conversation-memory'"
                    ).fetchone()[0],
                    "grants",
                )
                self.assertEqual(
                    tuple(
                        conn.execute(
                            "SELECT revoked_by_human_id,revoked_at FROM memory_grants WHERE id='revoked-grant'"
                        ).fetchone()
                    ),
                    (human_id, 21),
                )


if __name__ == "__main__":
    unittest.main()
