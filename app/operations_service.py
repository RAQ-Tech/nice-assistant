from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import zipfile

from app.auth import redact_sensitive_text
from app.database import create_verified_backup, upgrade_database
from app.repositories import now_ts
from app.service_errors import NotFoundError
from app.storage import BackupStore, safe_name


class OperationsService:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.backups = BackupStore(
            db_path=config.database_path,
            settings_json=config.settings_json,
            backup_dir=config.backup_dir,
            media_dirs=(
                ("audio", config.audio_dir),
                ("images", config.image_dir),
                ("videos", config.video_dir),
                ("stt_recordings", config.stt_recordings_dir),
                ("identity_references", config.identity_reference_dir),
            ),
            snapshot_limit=config.backup_snapshot_limit,
            now_ts=now_ts,
            logger=logger,
            log_event=self.log,
        )

    def startup_maintenance(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        target = self.config.archive_dir / "db_backups" / f"nice_assistant_{stamp}.db"
        if not target.exists() and self.config.database_path.exists():
            create_verified_backup(self.config.database_path, target)
        self._prune_count(self.config.archive_dir / "db_backups", "*.db", self.config.daily_database_backup_limit)
        self._prune_age(self.config.archive_dir / "audio", self.config.audio_archive_retention_days)
        self._prune_age(self.config.stt_recordings_dir, self.config.stt_recording_retention_days)
        self._prune_age(self.config.archive_dir / "logs", self.config.log_archive_retention_days)

    def log(self, event_type: str, message: str, **context) -> None:
        safe_context = {key: redact_sensitive_text(str(value)) for key, value in context.items() if value is not None}
        self.logger.info("event=%s message=%s context=%s", event_type, message, safe_context)

    def client_event(self, user_id: str | None, payload: dict) -> None:
        self.log(
            "client.event",
            str(payload.get("message") or payload.get("event") or "browser event")[:500],
            user_id=user_id,
            level=str(payload.get("level") or "info")[:20],
            detail=str(payload.get("detail") or "")[:1000],
        )

    def list_backups(self) -> list[dict]:
        return self.backups.list_backup_snapshots()

    def create_backup(self, include_media: bool) -> dict:
        return self.backups.create_backup_snapshot(include_media=include_media)

    def verify_backup(self, name: str) -> dict:
        path = self.backup_path(name)
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            for value in names:
                member = Path(value)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("backup contains an unsafe archive path")
            if "nice_assistant.db" not in names or "manifest.json" not in names:
                raise ValueError("backup is missing its database or manifest")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("app") != "nice-assistant" or manifest.get("database") != "nice_assistant.db":
                raise ValueError("backup manifest is not a Nice Assistant snapshot")
            with tempfile.TemporaryDirectory(prefix="nice-assistant-restore-drill-") as tmp:
                restored = Path(tmp) / "nice_assistant.db"
                with archive.open("nice_assistant.db") as source, restored.open("wb") as target:
                    shutil.copyfileobj(source, target)
                with closing(sqlite3.connect(restored)) as connection:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError("backup database integrity check failed")
                upgrade_database(restored)
                with closing(sqlite3.connect(restored)) as connection:
                    revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        return {
            "ok": True,
            "name": path.name,
            "database_integrity": "ok",
            "migration_revision": revision,
            "entry_count": len(names),
            "include_media": bool(manifest.get("includeMedia")),
        }

    def backup_path(self, name: str) -> Path:
        path = self.backups.backup_path_for_name(name)
        if not path or not path.exists() or not path.is_file():
            raise NotFoundError()
        return path

    def delete_backup(self, name: str) -> None:
        self.backup_path(name).unlink()

    def diagnostic_log(self, user_id: str) -> tuple[str, bytes]:
        target = self.config.log_dir / "events.log"
        if not target.exists():
            raise NotFoundError("log file unavailable")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"nice-assistant-events-{safe_name(user_id, 'user')}-{stamp}.txt"
        content = redact_sensitive_text(target.read_text(encoding="utf-8", errors="replace")).encode("utf-8")
        return filename, content

    def readiness(self) -> dict:
        database_ok = False
        try:
            database_uri = f"{self.config.database_path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(database_uri, timeout=2, uri=True)) as connection:
                database_ok = bool(connection.execute("SELECT version_num FROM alembic_version").fetchone())
        except (OSError, sqlite3.Error):
            database_ok = False
        storage = self.storage_report()
        storage_ok = storage["free_bytes"] >= self.config.minimum_free_storage_bytes
        directories_ok = all(item["available"] for item in storage["categories"].values())
        return {
            "ready": bool(database_ok and storage_ok and directories_ok),
            "components": {
                "database": "ready" if database_ok else "unavailable",
                "storage": "ready" if storage_ok and directories_ok else "degraded",
            },
            "free_storage_bytes": storage["free_bytes"],
            "minimum_free_storage_bytes": self.config.minimum_free_storage_bytes,
        }

    def storage_report(self) -> dict:
        categories = {}
        roots = {
            "audio_hot": self.config.audio_dir,
            "audio_archive": self.config.archive_dir / "audio",
            "images": self.config.image_dir,
            "videos": self.config.video_dir,
            "stt_recordings": self.config.stt_recordings_dir,
            "identity_references": self.config.identity_reference_dir,
            "backup_snapshots": self.config.backup_dir,
            "database_backups": self.config.archive_dir / "db_backups",
            "logs": self.config.log_dir,
        }
        for label, root in roots.items():
            count = 0
            size = 0
            try:
                for path in root.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        count += 1
                        size += path.stat().st_size
                available = root.exists() and root.is_dir()
            except OSError:
                available = False
            categories[label] = {"files": count, "bytes": size, "available": available}
        try:
            free = shutil.disk_usage(self.config.data_dir).free
        except OSError:
            free = 0
        try:
            archive_free = shutil.disk_usage(self.config.archive_dir).free
        except OSError:
            archive_free = 0
        return {
            "free_bytes": free,
            "archive_free_bytes": archive_free,
            "categories": categories,
            "retention": {
                "audio_hot_limit": self.config.audio_hot_limit,
                "audio_archive_days": self.config.audio_archive_retention_days,
                "stt_recording_days": self.config.stt_recording_retention_days,
                "log_archive_days": self.config.log_archive_retention_days,
                "daily_database_backup_limit": self.config.daily_database_backup_limit,
                "backup_snapshot_limit": self.config.backup_snapshot_limit,
                "zero_days_disables_age_pruning": True,
            },
        }

    def _prune_age(self, root: Path, retention_days: int) -> None:
        if retention_days <= 0 or not root.exists():
            return
        cutoff = time.time() - retention_days * 86400
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    self.log("retention.prune", "expired artifact pruned", category=root.name)
            except OSError as exc:
                self.logger.warning("retention prune failed category=%s error=%s", root.name, exc.__class__.__name__)

    def _prune_count(self, root: Path, pattern: str, limit: int) -> None:
        paths = sorted(
            (path for path in root.glob(pattern) if path.is_file() and not path.is_symlink()),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
        while len(paths) > limit:
            path = paths.pop(0)
            try:
                path.unlink()
                self.log("retention.prune", "old snapshot pruned", category=root.name)
            except OSError as exc:
                self.logger.warning("snapshot prune failed category=%s error=%s", root.name, exc.__class__.__name__)
