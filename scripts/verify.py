#!/usr/bin/env python3
"""Deterministic repository verification entrypoint."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str], env: dict[str, str] | None = None) -> None:
    print(f"\n== {label} ==", flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1, help="number of complete unit/API suite runs")
    parser.add_argument("--skip-smoke", action="store_true", help="skip the process-level smoke check")
    parser.add_argument("--skip-browser-e2e", action="store_true", help="skip Playwright browser journeys")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    env = os.environ.copy()
    env.setdefault("LOG_LEVEL", "CRITICAL")

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is required; install Node.js 24 or newer")
    run("Browser typecheck", [npm, "run", "frontend:typecheck"], env)
    run("Browser unit tests", [npm, "run", "frontend:test"], env)
    run("Browser production build", [npm, "run", "frontend:build"], env)

    run("Public repository privacy audit", [sys.executable, "scripts/audit_public_repo.py"], env)

    run(
        "Python compile",
        [sys.executable, "-m", "compileall", "-q", "app", "migrations", "tests", "scripts"],
        env,
    )

    if importlib.util.find_spec("ruff"):
        run(
            "Static analysis",
            [sys.executable, "-m", "ruff", "check", "app", "migrations", "tests", "scripts"],
            env,
        )
        run(
            # Everything, rather than a curated list. The list was four files
            # away from being the whole repository, and its real cost was that
            # editing anything outside it failed here at the last gate instead
            # of at the first.
            "Formatter check",
            [sys.executable, "-m", "ruff", "format", "--check", "app", "migrations", "tests", "scripts"],
            env,
        )
    else:
        print("\nWARNING: ruff is unavailable; install the dev extra with: pip install -e .[dev]", flush=True)

    coverage_available = importlib.util.find_spec("coverage") is not None
    for index in range(args.repeat):
        label = f"Unit/API suite {index + 1}/{args.repeat}"
        if index == 0 and coverage_available:
            run(
                label,
                [sys.executable, "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "tests", "-v"],
                env,
            )
            run("Coverage report", [sys.executable, "-m", "coverage", "report"], env)
        else:
            run(label, [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env)

    if not args.skip_smoke:
        run("Process smoke", [sys.executable, "scripts/smoke_check.py"], env)

    if not args.skip_browser_e2e:
        run("Browser journeys", [npm, "run", "frontend:e2e"], env)

    run("Human-experience scenarios", [sys.executable, "scripts/evaluate_human_experience.py"], env)

    print("\nVerification passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
