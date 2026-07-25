#!/usr/bin/env python3
"""Drill a frozen exact-ID memory reset on an internal snapshot copy only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.private_memory_baseline import BaselineError, drill_memory_reset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Nice Assistant snapshot ZIP")
    parser.add_argument("baseline_json", type=Path, help="private baseline JSON created by the export tool")
    args = parser.parse_args()
    try:
        result = drill_memory_reset(args.snapshot, args.baseline_json)
    except BaselineError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
