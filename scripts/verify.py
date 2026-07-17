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
            "Formatter check for new foundation modules",
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "app/api_v1.py",
                "app/application.py",
                "app/asgi.py",
                "app/capability_contracts.py",
                "app/capability_service.py",
                "app/conversation_service.py",
                "app/context_service.py",
                "app/compreface_identity_provider.py",
                "app/database.py",
                "app/job_queue.py",
                "app/job_service.py",
                "app/identity_contracts.py",
                "app/identity_conditioning.py",
                "app/identity_api.py",
                "app/identity_images.py",
                "app/identity_service.py",
                "app/media_adapters.py",
                "app/media_clients.py",
                "app/media_catalog_service.py",
                "app/media_service.py",
                "app/media_planner.py",
                "app/memory_service.py",
                "app/models.py",
                "app/ollama_provider.py",
                "app/observability.py",
                "app/operations_service.py",
                "app/provider_contracts.py",
                "app/provider_registry.py",
                "app/provider_service.py",
                "app/repositories.py",
                "app/resource_coordination.py",
                "app/resource_providers.py",
                "app/resource_service.py",
                "app/runtime.py",
                "app/secret_store.py",
                "app/security.py",
                "app/service_errors.py",
                "app/speech_clients.py",
                "app/speech_service.py",
                "app/turn_events.py",
                "app/typed_settings.py",
                "app/task_contracts.py",
                "app/task_model_service.py",
                "migrations",
                "scripts/smoke_check.py",
                "scripts/evaluate_task_models.py",
                "scripts/evaluate_human_experience.py",
                "scripts/backup_restore_drill.py",
                "scripts/audit_public_repo.py",
                "scripts/verify.py",
                "tests/support.py",
                "tests/test_api_contracts.py",
                "tests/test_asgi_api.py",
                "tests/test_async_jobs.py",
                "tests/test_browser_architecture.py",
                "tests/test_capabilities.py",
                "tests/test_context_service.py",
                "tests/test_deployment_guard.py",
                "tests/test_unraid_installer_acceptance.py",
                "tests/test_human_experience_scenarios.py",
                "tests/test_database_foundation.py",
                "tests/test_database_foundation.py",
                "tests/test_identity_provider.py",
                "tests/test_image_error_mapping.py",
                "tests/test_lan_hardening.py",
                "tests/test_media_catalog.py",
                "tests/test_persona_identity.py",
                "tests/test_memory_v2.py",
                "tests/test_module_contracts.py",
                "tests/test_ollama_provider.py",
                "tests/test_provider_readiness.py",
                "tests/test_production_hardening.py",
                "tests/test_public_repo_audit.py",
                "tests/test_resource_coordination.py",
                "tests/test_resource_providers.py",
                "tests/test_turn_events.py",
                "tests/test_task_models.py",
                "tests/test_task_model_evaluation.py",
            ],
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
