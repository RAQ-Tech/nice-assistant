#!/usr/bin/env python3
"""Run deterministic human-experience scenarios without contacting live providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

BACKEND_SCENARIOS = (
    "tests.test_context_service.ContextServiceTests.test_two_hundred_turn_transcript_stays_inside_budget",
    "tests.test_human_experience_scenarios.HumanExperienceScenarioTests.test_a_pending_correction_cannot_silently_replace_approved_memory",
    "tests.test_human_experience_scenarios.HumanExperienceScenarioTests.test_persona_switching_changes_the_next_turn_without_leaking_the_prior_persona",
    "tests.test_memory_v2.MemoryV2Tests.test_only_approved_scoped_fts_results_reach_context",
    "tests.test_async_jobs.AsyncJobTests.test_premature_persona_media_claim_is_never_streamed_or_persisted",
    "tests.test_async_jobs.AsyncJobTests.test_title_and_capability_followups_start_independently_after_reply_delivery",
    "tests.test_context_service.ContextServiceTests.test_summary_failure_degrades_without_failing_main_turn",
    "tests.test_memory_v2.MemoryV2Tests.test_extraction_failure_never_changes_a_completed_turn",
    "tests.test_capabilities.CapabilityTests.test_explicit_image_request_runs_automatically_as_a_reload_safe_attachment",
    "tests.test_capabilities.CapabilityTests.test_failed_chat_attachment_can_retry_against_current_policy",
    "tests.test_media_catalog.MediaCatalogTests.test_multiple_configured_backends_select_a_ready_fallback_deterministically",
)

FRONTEND_SCENARIO_FILES = (
    "frontend/tests/chat.test.ts",
    "frontend/tests/playback.test.ts",
    "frontend/tests/chat_attachments.test.ts",
    "frontend/tests/composer_state.test.ts",
)


def run(command: list[str]) -> bool:
    return subprocess.run(command, cwd=ROOT, check=False).returncode == 0


def main() -> int:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is required to evaluate browser interaction scenarios")

    backend_passed = run([sys.executable, "-m", "unittest", "-v", *BACKEND_SCENARIOS])
    frontend_passed = run([npm, "exec", "--", "vitest", "run", "--config", "vite.config.ts", *FRONTEND_SCENARIO_FILES])
    result = {
        "passed": backend_passed and frontend_passed,
        "scenario_families": {
            "conversation_integrity": [
                "long conversations",
                "corrections",
                "persona switching",
                "memory boundaries",
                "truthful media claims",
                "reply critical-path ordering",
            ],
            "provider_and_media_resilience": [
                "task-provider degradation",
                "media failure and retry",
                "reload-safe attachments",
                "configured image-provider fallback",
            ],
            "kokoro_and_browser_interaction": [
                "completed-file Kokoro cleanup and interruption",
                "image blur and compact failures",
                "composer availability and title reconciliation",
            ],
        },
        "backend_passed": backend_passed,
        "frontend_passed": frontend_passed,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
