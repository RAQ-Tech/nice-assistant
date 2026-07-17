import json
import os
import secrets
import sqlite3
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.service_errors import InvalidArtifactError, StorageCapacityError


BACKUP_NAME_RE = r"^nice-assistant-snapshot-\d{8}_\d{6}-[a-f0-9]{8}\.zip$"


def write_artifact_atomic(path: Path, content: bytes, *, mode: int | None = None) -> None:
    if not content:
        raise InvalidArtifactError()
    if mode is not None and not 0 <= mode <= 0o777:
        raise ValueError("artifact mode must contain only file permission bits")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise StorageCapacityError() from exc


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def safe_name(name, fallback):
    import re

    candidate = (name or "").strip().replace(" ", "_")
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", candidate)
    return candidate or fallback


def backup_name_from_api_path(path, expect_download=False):
    request_path = urllib.parse.urlparse(path).path
    prefix = "/api/v1/admin/backups/"
    if not request_path.startswith(prefix):
        return None
    tail = request_path[len(prefix) :]
    if expect_download:
        suffix = "/download"
        if not tail.endswith(suffix):
            return None
        tail = tail[: -len(suffix)]
    if not tail:
        return None
    return urllib.parse.unquote(tail)


@dataclass
class BackupStore:
    db_path: Path
    settings_json: Path
    backup_dir: Path
    media_dirs: tuple
    snapshot_limit: int
    now_ts: object
    logger: object = None
    log_event: object = None

    def backup_snapshot_filename(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"nice-assistant-snapshot-{stamp}-{secrets.token_hex(4)}.zip"

    def backup_path_for_name(self, name):
        import re

        safe_name_value = os.path.basename(str(name or ""))
        if safe_name_value != str(name or "") or not re.match(BACKUP_NAME_RE, safe_name_value):
            return None
        backup_root = self.backup_dir.resolve()
        candidate = (self.backup_dir / safe_name_value).resolve()
        if candidate.parent != backup_root:
            return None
        return candidate

    def sqlite_backup_to_path(self, target_path):
        source = sqlite3.connect(self.db_path)
        dest = sqlite3.connect(target_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

    def backup_snapshot_metadata(self, path):
        stat = path.stat()
        created_at = int(stat.st_mtime)
        include_media = None
        try:
            with zipfile.ZipFile(path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                created_at = int(manifest.get("createdAt") or created_at)
                include_media = bool(manifest.get("includeMedia"))
        except Exception:
            include_media = None
        return {
            "name": path.name,
            "size": stat.st_size,
            "created_at": created_at,
            "created_at_iso": datetime.fromtimestamp(created_at, timezone.utc).isoformat(),
            "include_media": include_media,
            "download_url": f"/api/v1/admin/backups/{urllib.parse.quote(path.name)}/download",
        }

    def list_backup_snapshots(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for path in self.backup_dir.glob("*.zip"):
            if not path.is_file() or not self.backup_path_for_name(path.name):
                continue
            try:
                items.append(self.backup_snapshot_metadata(path))
            except OSError:
                continue
        return sorted(items, key=lambda item: (item.get("created_at") or 0, item.get("name") or ""), reverse=True)

    def iter_backup_media_files(self):
        for label, root in self.media_dirs:
            if not root.exists():
                continue
            root_resolved = root.resolve()
            for path in root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root_resolved)
                    rel = path.relative_to(root)
                except (OSError, ValueError):
                    continue
                archive_name = Path("data") / label / rel
                yield path, archive_name.as_posix()

    def prune_backup_snapshots(self, limit=None):
        limit = self.snapshot_limit if limit is None else int(limit)
        if limit <= 0:
            return
        paths = [
            path for path in self.backup_dir.glob("*.zip") if path.is_file() and self.backup_path_for_name(path.name)
        ]
        paths.sort(key=lambda p: (p.stat().st_mtime, p.name))
        while len(paths) > limit:
            oldest = paths.pop(0)
            try:
                oldest.unlink()
                if self.log_event:
                    self.log_event("backup.prune", "old backup snapshot pruned", backup_name=oldest.name)
            except OSError as exc:
                if self.logger:
                    self.logger.warning("failed to prune backup snapshot name=%s error=%s", oldest.name, exc)

    def create_backup_snapshot(self, include_media=False):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = self.backup_snapshot_filename()
        final_path = self.backup_path_for_name(name)
        if final_path is None:
            raise ValueError("invalid generated backup name")

        created_at = self.now_ts()
        created_iso = datetime.fromtimestamp(created_at, timezone.utc).isoformat()
        tmp_zip_handle = tempfile.NamedTemporaryFile(
            prefix=f".{name}.", suffix=".tmp", dir=self.backup_dir, delete=False
        )
        tmp_zip_path = Path(tmp_zip_handle.name)
        tmp_zip_handle.close()
        try:
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.backup_dir) as tmp_dir:
                db_backup_path = Path(tmp_dir) / "nice_assistant.db"
                self.sqlite_backup_to_path(db_backup_path)
                entry_count = 0
                media_dirs = [label for label, _root in self.media_dirs] if include_media else []
                manifest = {
                    "formatVersion": 1,
                    "app": "nice-assistant",
                    "name": name,
                    "createdAt": created_at,
                    "createdAtIso": created_iso,
                    "includeMedia": bool(include_media),
                    "database": "nice_assistant.db",
                    "settings": "settings.json" if self.settings_json.exists() else None,
                    "mediaDirs": media_dirs,
                }
                with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.write(db_backup_path, "nice_assistant.db")
                    entry_count += 1
                    if (
                        self.settings_json.exists()
                        and self.settings_json.is_file()
                        and not self.settings_json.is_symlink()
                    ):
                        zf.write(self.settings_json, "settings.json")
                        entry_count += 1
                    if include_media:
                        for file_path, archive_name in self.iter_backup_media_files():
                            zf.write(file_path, archive_name)
                            entry_count += 1
                    manifest["entryCount"] = entry_count + 1
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(tmp_zip_path, final_path)
            self.prune_backup_snapshots()
            return self.backup_snapshot_metadata(final_path)
        except Exception:
            try:
                tmp_zip_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
