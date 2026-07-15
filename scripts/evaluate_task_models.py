#!/usr/bin/env python3
"""Developer-only contract and behavior screening for local Ollama task models."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, is_dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ollama_provider import OllamaChatProvider  # noqa: E402
from app.provider_contracts import CancellationToken, ChatRequest, ProviderError  # noqa: E402
from app.task_contracts import (  # noqa: E402
    CAPABILITY_PLANNING,
    CONVERSATION_SUMMARY,
    MEMORY_EXTRACTION,
    TITLE_GENERATION,
    AvailableCapability,
    CapabilityPlanningTaskInput,
    MemoryExtractionTaskInput,
    SummaryTaskInput,
    TitleTaskInput,
    task_definition,
)


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    role: str
    task_input: Any
    assess: Callable[[Any], list[str]]


def _contains_all(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    missing = [term for term in terms if term.casefold() not in lowered]
    return [f"missing required concept: {term}" for term in missing]


def evaluation_cases() -> tuple[EvaluationCase, ...]:
    image = AvailableCapability("media.generate_image", "Generate image", "Create an image from a prompt.")
    return (
        EvaluationCase(
            "specific_title",
            TITLE_GENERATION,
            TitleTaskInput("Help me plan drip irrigation for my vegetable garden."),
            lambda output: (
                []
                if len(output.title.split()) >= 2 and output.title.casefold() not in {"new chat", "conversation"}
                else ["title is generic"]
            ),
        ),
        EvaluationCase(
            "summary_retains_corrections",
            CONVERSATION_SUMMARY,
            SummaryTaskInput(
                previous_summary="The user was considering a November trip.",
                transcript=(
                    "user: Correction: the trip is in December, not November.\n"
                    "assistant: Understood.\nuser: I still need to choose a venue."
                ),
            ),
            lambda output: _contains_all(output.summary, ("December", "venue")),
        ),
        EvaluationCase(
            "memory_extracts_stable_preferences",
            MEMORY_EXTRACTION,
            MemoryExtractionTaskInput("I live in Portland, Maine, and I prefer decaf coffee."),
            lambda output: (
                []
                if output.candidates
                and any(
                    "maine" in candidate.content.casefold() or "decaf" in candidate.content.casefold()
                    for candidate in output.candidates
                )
                else ["stable stated facts were not extracted"]
            ),
        ),
        EvaluationCase(
            "memory_excludes_credentials",
            MEMORY_EXTRACTION,
            MemoryExtractionTaskInput("My temporary API key is sk-not-a-real-evaluation-secret. Do not save it."),
            lambda output: [] if not output.candidates else ["credential-like data was extracted"],
        ),
        EvaluationCase(
            "capability_skips_ordinary_text",
            CAPABILITY_PLANNING,
            CapabilityPlanningTaskInput(
                user_text="Explain why leaves change color.",
                assistant_text="Leaves change as chlorophyll breaks down.",
                available_capabilities=(image,),
            ),
            lambda output: [] if not output.requests else ["ordinary text incorrectly requested a capability"],
        ),
        EvaluationCase(
            "capability_skips_literal_reply_contract",
            CAPABILITY_PLANNING,
            CapabilityPlanningTaskInput(
                user_text="Reply with exactly: managed reclamation passed",
                assistant_text="managed reclamation passed",
                available_capabilities=(image,),
            ),
            lambda output: [] if not output.requests else ["literal reply incorrectly requested a capability"],
        ),
        EvaluationCase(
            "capability_skips_literal_outage_reply",
            CAPABILITY_PLANNING,
            CapabilityPlanningTaskInput(
                user_text="Reply with exactly: speech outage chat survived",
                assistant_text="speech outage chat survived",
                available_capabilities=(image,),
            ),
            lambda output: [] if not output.requests else ["literal outage reply requested a capability"],
        ),
        EvaluationCase(
            "capability_requests_semantic_image",
            CAPABILITY_PLANNING,
            CapabilityPlanningTaskInput(
                user_text="Create an image of a lighthouse in a storm.",
                assistant_text="I can prepare that image request.",
                available_capabilities=(image,),
            ),
            lambda output: (
                []
                if len(output.requests) == 1
                and output.requests[0].capability_key == "media.generate_image"
                and "lighthouse" in output.requests[0].prompt.casefold()
                else ["semantic image request was not planned correctly"]
            ),
        ),
    )


def assess_case(case: EvaluationCase, output: Any) -> list[str]:
    return case.assess(output)


def run_case(provider, model: str, case: EvaluationCase, timeout_seconds: float, show_output: bool) -> dict:
    definition = task_definition(case.role)
    started = time.monotonic()
    try:
        request = ChatRequest(
            model=model,
            messages=definition.messages(case.task_input),
            options={
                "num_predict": definition.default_max_output_tokens,
                "temperature": definition.default_temperature,
            },
            response_format=definition.response_schema(case.task_input),
            timeout_seconds=timeout_seconds,
        )
        raw = provider.generate(request, CancellationToken())
        output = definition.parse_output(raw, case.task_input, definition.default_max_output_tokens)
        failures = assess_case(case, output)
        result = {
            "name": case.name,
            "role": case.role,
            "passed": not failures,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "failures": failures,
        }
        if show_output:
            result["output"] = asdict(output) if is_dataclass(output) else output
        return result
    except (ProviderError, ValueError) as exc:
        return {
            "name": case.name,
            "role": case.role,
            "passed": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "failures": [f"{exc.__class__.__name__}: {str(exc)}"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", help="installed Ollama model; defaults to the first listed model")
    parser.add_argument("--role", action="append", choices=[case.role for case in evaluation_cases()])
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="include generated task output in stdout; output is omitted by default for privacy",
    )
    args = parser.parse_args()
    provider = OllamaChatProvider(args.base_url, timeout_seconds=args.timeout)
    models = provider.list_models()
    model = args.model or (models[0] if models else None)
    if not model:
        parser.error("Ollama returned no installed models; pass --base-url for the intended LAN service")
    if args.model and args.model not in models:
        parser.error(f"model is not installed at the selected Ollama service: {args.model}")
    selected_roles = set(args.role or [])
    cases = [case for case in evaluation_cases() if not selected_roles or case.role in selected_roles]
    results = [run_case(provider, model, case, max(1.0, args.timeout), args.show_output) for case in cases]
    payload = {
        "model": model,
        "base_url": args.base_url,
        "passed": all(result["passed"] for result in results),
        "passed_cases": sum(bool(result["passed"]) for result in results),
        "total_cases": len(results),
        "results": results,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
