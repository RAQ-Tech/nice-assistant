#!/usr/bin/env python3
"""Create private Memory v3 baseline artifacts from a Nice Assistant snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.private_memory_baseline import BaselineError, export_memory_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Nice Assistant snapshot ZIP")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="private directory outside the repository",
    )
    parser.add_argument("--owner-id", help="owner ID; required when a snapshot contains multiple accounts")
    args = parser.parse_args()
    try:
        result = export_memory_baseline(
            args.snapshot,
            output_dir=args.output_dir,
            owner_id=args.owner_id,
        )
    except BaselineError as exc:
        parser.error(str(exc))
    print(json.dumps(result.content_free_response(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
