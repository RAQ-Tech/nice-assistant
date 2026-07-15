#!/usr/bin/env python3
"""Validate a snapshot through safe extraction, SQLite integrity, and migration on a temporary copy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from app.operations_service import OperationsService
from app.runtime import AppConfig


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="path to a Nice Assistant snapshot ZIP")
    args = parser.parse_args()
    source = args.snapshot.resolve()
    if not source.exists() or not source.is_file():
        parser.error("snapshot does not exist")
    with tempfile.TemporaryDirectory(prefix="nice-assistant-restore-drill-") as tmp:
        root = Path(tmp)
        config = AppConfig(data_dir=root / "data", archive_dir=root / "archives")
        config.ensure_directories()
        target = config.backup_dir / source.name
        shutil.copy2(source, target)
        result = OperationsService(config, _Logger()).verify_backup(target.name)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
