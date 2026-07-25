"""Pure loading and assessment for the observe-only Memory v2 extraction baseline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import unicodedata
from typing import Callable
from urllib.parse import urlsplit

from app.auth import redact_sensitive_text
from app.memory_service import EXTRACTOR_VERSION, memory_candidate_is_sensitive
from app.task_contracts import (
    MEMORY_EXTRACTION,
    MemoryCandidate,
    MemoryExtractionTaskInput,
    MemoryExtractionTaskOutput,
    task_definition,
)


BASELINE_CORPUS_PATH = Path(__file__).resolve().parent / "evaluation_data" / "memory-v2-extraction-baseline.v1.json"
BASELINE_REPORT_SCHEMA_VERSION = 2
BASELINE_EVALUATOR_VERSION = "memory-v2-observe-only-v2"
BASELINE_CANDIDATE_LIMIT = 5
REQUIRED_BASELINE_TAGS = {
    "acceptance",
    "credential",
    "negation",
    "question",
    "stable",
    "unsupported",
    "vague",
}
_EXPECTATIONS = {"extract", "abstain"}
_CORPUS_FIELDS = {
    "schema_version",
    "corpus_id",
    "corpus_version",
    "synthetic",
    "description",
    "synthetic_value_lexicon",
    "cases",
}
_CASE_FIELDS = {
    "id",
    "user_text",
    "tags",
    "expectation",
    "allowed_values",
    "forbidden",
    "facts",
}
_FACT_FIELDS = {"id", "example", "required"}


@dataclass(frozen=True)
class BaselineFact:
    id: str
    example: str
    required: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class MemoryBaselineCase:
    id: str
    user_text: str
    tags: tuple[str, ...]
    expectation: str
    allowed_values: tuple[str, ...]
    forbidden: tuple[str, ...]
    facts: tuple[BaselineFact, ...]


@dataclass(frozen=True)
class MemoryBaselineCorpus:
    schema_version: int
    corpus_id: str
    corpus_version: str
    description: str
    synthetic_value_lexicon: tuple[str, ...]
    cases: tuple[MemoryBaselineCase, ...]
    sha256: str


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("’", "'").replace("‘", "'")
    return " ".join(text.casefold().split())


def _phrase_present(text: str, phrase: str) -> bool:
    normalized_phrase = _normalized(phrase)
    if not normalized_phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", text) is not None


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def baseline_reproducibility(*, timeout_seconds: float = 90.0) -> dict:
    """Describe and hash the exact local task contract exercised by this evaluator."""

    definition = task_definition(MEMORY_EXTRACTION)
    task_input = MemoryExtractionTaskInput(
        user_text="SYNTHETIC_CONTRACT_HASH_PROBE",
        max_candidates=BASELINE_CANDIDATE_LIMIT,
    )
    messages = definition.messages(task_input)
    system_prompt = messages[0]["content"]
    payload_probe = messages[1]["content"]
    response_schema = definition.response_schema(task_input)
    generation_options = {
        "num_predict": definition.default_max_output_tokens,
        "temperature": definition.default_temperature,
    }
    component_hashes = {
        "system_prompt_sha256": _sha256_text(system_prompt),
        "payload_probe_sha256": _sha256_text(payload_probe),
        "response_schema_sha256": _sha256_text(_canonical_json(response_schema)),
        "generation_options_sha256": _sha256_text(_canonical_json(generation_options)),
    }
    aggregate_contract = {
        "task_role": MEMORY_EXTRACTION,
        "extractor_version": EXTRACTOR_VERSION,
        "candidate_limit": BASELINE_CANDIDATE_LIMIT,
        "component_hashes": component_hashes,
    }
    source_hashes = {
        "assessor_source_sha256": _sha256_file(Path(__file__)),
        "runner_source_sha256": _sha256_file(Path(__file__).with_name("evaluate_task_models.py")),
    }
    execution_options = {"timeout_seconds": max(1.0, float(timeout_seconds))}
    evaluation_bundle = {
        "evaluator_version": BASELINE_EVALUATOR_VERSION,
        "aggregate_contract_sha256": _sha256_text(_canonical_json(aggregate_contract)),
        "source_hashes": source_hashes,
        "execution_options": execution_options,
    }
    return {
        "evaluator_version": BASELINE_EVALUATOR_VERSION,
        "report_schema_version": BASELINE_REPORT_SCHEMA_VERSION,
        "task_role": MEMORY_EXTRACTION,
        "extractor_version": EXTRACTOR_VERSION,
        "candidate_limit": BASELINE_CANDIDATE_LIMIT,
        "generation_options": generation_options,
        "execution_options": execution_options,
        "source_hashes": source_hashes,
        "contract_hashes": {
            **component_hashes,
            "aggregate_contract_sha256": _sha256_text(_canonical_json(aggregate_contract)),
            "evaluation_bundle_sha256": _sha256_text(_canonical_json(evaluation_bundle)),
        },
    }


def safe_endpoint_descriptor(base_url: str) -> dict:
    """Classify an endpoint without retaining its address, userinfo, path, or query."""

    normalized = str(base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
        hostname = (parsed.hostname or "").casefold()
    except ValueError:
        hostname = ""
    endpoint_class = "unclassified"
    if hostname in {"localhost", "localhost.localdomain"}:
        endpoint_class = "loopback"
    elif hostname:
        try:
            address = ipaddress.ip_address(hostname)
            if address.is_loopback:
                endpoint_class = "loopback"
            elif address.is_private or address.is_link_local:
                endpoint_class = "private_lan"
            else:
                endpoint_class = "other_network"
        except ValueError:
            if hostname.endswith((".lan", ".local")) or "." not in hostname:
                endpoint_class = "private_lan_hostname"
            else:
                endpoint_class = "other_network"
    return {
        "class": endpoint_class,
        "identifier": {
            "available": False,
            "value": None,
        },
    }


def _required_text(value, *, label: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _string_list(value, *, label: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a {'nonempty ' if not allow_empty else ''}list")
    result = []
    for item in value:
        text = _required_text(item, label=f"{label} item")
        if text in result:
            raise ValueError(f"{label} contains a duplicate")
        result.append(text)
    return tuple(result)


def _strict_mapping(value, allowed_fields: set[str], *, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != allowed_fields:
        raise ValueError(f"{label} has invalid fields")
    return value


def _load_fact(value, *, case_id: str) -> BaselineFact:
    data = _strict_mapping(value, _FACT_FIELDS, label=f"fact in {case_id}")
    fact_id = _required_text(data["id"], label=f"fact id in {case_id}")
    example = _required_text(data["example"], label=f"fact example in {case_id}")
    required = data["required"]
    if not isinstance(required, list) or not required:
        raise ValueError(f"fact {fact_id} in {case_id} requires concept groups")
    groups = tuple(_string_list(group, label=f"fact {fact_id} concept group", allow_empty=False) for group in required)
    return BaselineFact(fact_id, example, groups)


def load_memory_baseline_corpus(path: Path = BASELINE_CORPUS_PATH) -> MemoryBaselineCorpus:
    """Load and strictly validate the committed synthetic corpus."""

    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("memory baseline corpus is not valid UTF-8 JSON") from exc
    data = _strict_mapping(payload, _CORPUS_FIELDS, label="memory baseline corpus")
    if data["schema_version"] != 1:
        raise ValueError("unsupported memory baseline corpus schema")
    if data["synthetic"] is not True:
        raise ValueError("memory baseline corpus must be explicitly synthetic")
    corpus_id = _required_text(data["corpus_id"], label="corpus id")
    corpus_version = _required_text(data["corpus_version"], label="corpus version")
    description = _required_text(data["description"], label="corpus description")
    lexicon = _string_list(data["synthetic_value_lexicon"], label="synthetic value lexicon")
    lexicon_norms = {_normalized(value) for value in lexicon}
    if len(lexicon_norms) != len(lexicon):
        raise ValueError("synthetic value lexicon contains normalized duplicates")

    raw_cases = data["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("memory baseline corpus requires cases")
    cases = []
    case_ids = set()
    fact_ids = set()
    observed_tags = set()
    for value in raw_cases:
        case_data = _strict_mapping(value, _CASE_FIELDS, label="memory baseline case")
        case_id = _required_text(case_data["id"], label="case id")
        if case_id in case_ids:
            raise ValueError(f"duplicate memory baseline case id: {case_id}")
        case_ids.add(case_id)
        user_text = _required_text(case_data["user_text"], label=f"user text in {case_id}")
        tags = _string_list(case_data["tags"], label=f"tags in {case_id}", allow_empty=False)
        observed_tags.update(tags)
        expectation = str(case_data["expectation"] or "").strip()
        if expectation not in _EXPECTATIONS:
            raise ValueError(f"invalid expectation in {case_id}")
        allowed_values = _string_list(case_data["allowed_values"], label=f"allowed values in {case_id}")
        unknown_allowed_values = {_normalized(item) for item in allowed_values} - lexicon_norms
        if unknown_allowed_values:
            raise ValueError(f"case {case_id} allows values absent from the synthetic lexicon")
        forbidden = _string_list(case_data["forbidden"], label=f"forbidden claims in {case_id}")
        facts = tuple(_load_fact(item, case_id=case_id) for item in case_data["facts"])
        case_fact_examples = set()
        for fact in facts:
            if fact.id in fact_ids:
                raise ValueError(f"duplicate memory baseline fact id: {fact.id}")
            fact_ids.add(fact.id)
            normalized_example = _normalized(fact.example)
            if normalized_example in case_fact_examples:
                raise ValueError(f"duplicate memory baseline canonical fact output: {fact.example}")
            case_fact_examples.add(normalized_example)
        if expectation == "extract" and not facts:
            raise ValueError(f"extract case {case_id} requires expected facts")
        if expectation == "abstain" and facts:
            raise ValueError(f"abstain case {case_id} cannot define expected facts")
        if (
            "credential" in tags
            and "test_only" not in _normalized(user_text)
            and "not-a-real" not in _normalized(user_text)
        ):
            raise ValueError(f"credential case {case_id} must use an obvious test-only value")
        cases.append(
            MemoryBaselineCase(
                case_id,
                user_text,
                tags,
                expectation,
                allowed_values,
                forbidden,
                facts,
            )
        )
    missing_tags = REQUIRED_BASELINE_TAGS - observed_tags
    if missing_tags:
        raise ValueError(f"memory baseline corpus is missing required tags: {', '.join(sorted(missing_tags))}")
    return MemoryBaselineCorpus(
        schema_version=1,
        corpus_id=corpus_id,
        corpus_version=corpus_version,
        description=description,
        synthetic_value_lexicon=lexicon,
        cases=tuple(cases),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _fact_lexically_matches(text: str, fact: BaselineFact) -> bool:
    return all(any(_phrase_present(text, alternative) for alternative in group) for group in fact.required)


def _fact_is_exactly_allowlisted(text: str, fact: BaselineFact) -> bool:
    return text == _normalized(fact.example)


def raw_exact_duplicate_candidate_count(raw: str) -> int:
    """Count exact normalized duplicate candidates before the task parser deduplicates them."""

    try:
        payload = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return 0
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return 0
    seen = set()
    duplicates = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = _normalized(candidate.get("content"))
        if not content:
            continue
        if content in seen:
            duplicates += 1
        else:
            seen.add(content)
    return duplicates


def _confidence_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "minimum": None, "maximum": None, "mean": None}
    return {
        "count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": round(sum(values) / len(values), 6),
    }


def assess_memory_baseline_case(
    corpus: MemoryBaselineCorpus,
    case: MemoryBaselineCase,
    output: MemoryExtractionTaskOutput,
    *,
    show_output: bool = False,
    sensitive_filter: Callable[[str], bool] = memory_candidate_is_sensitive,
    parser_deduplicated_exact_candidate_count: int = 0,
) -> dict:
    """Assess parsed Memory v2 output using a conservative grounding lower bound."""

    allowed_values = {_normalized(value) for value in case.allowed_values}
    raw_lexical_fact_matches = {fact.id: 0 for fact in case.facts}
    post_filter_lexical_fact_matches = {fact.id: 0 for fact in case.facts}
    raw_strict_fact_matches = {fact.id: 0 for fact in case.facts}
    post_filter_strict_fact_matches = {fact.id: 0 for fact in case.facts}
    candidate_observations = []
    issues = []
    strict_grounded_confidences = []
    non_strict_confidences = []
    seen_strict_fact_ids = set()

    for index, candidate in enumerate(output.candidates):
        text = _normalized(candidate.content)
        sensitive = bool(sensitive_filter(candidate.content))
        would_persist = not sensitive
        candidate_issues = []
        unexpected_values = [
            value
            for value in corpus.synthetic_value_lexicon
            if _phrase_present(text, value) and _normalized(value) not in allowed_values
        ]
        if unexpected_values:
            candidate_issues.extend(f"invented_value:{_normalized(value)}" for value in unexpected_values)
        forbidden_claims = [claim for claim in case.forbidden if _phrase_present(text, claim)]
        if forbidden_claims:
            candidate_issues.extend(f"forbidden_claim:{_normalized(claim)}" for claim in forbidden_claims)

        lexically_matched_facts = [fact.id for fact in case.facts if _fact_lexically_matches(text, fact)]
        exactly_allowlisted_facts = [fact.id for fact in case.facts if _fact_is_exactly_allowlisted(text, fact)]
        for fact_id in lexically_matched_facts:
            raw_lexical_fact_matches[fact_id] += 1
            if would_persist:
                post_filter_lexical_fact_matches[fact_id] += 1

        duplicate_exact_fact_ids = [fact_id for fact_id in exactly_allowlisted_facts if fact_id in seen_strict_fact_ids]
        if sensitive:
            grounding_status = "prohibited"
            candidate_issues.append("sensitive_candidate_raw")
        elif case.expectation == "abstain":
            grounding_status = "unsupported"
            candidate_issues.append("unexpected_candidate")
        elif unexpected_values or forbidden_claims:
            grounding_status = "contradicted"
            candidate_issues.append("contradicted_candidate")
        elif duplicate_exact_fact_ids:
            grounding_status = "duplicate"
            candidate_issues.extend(f"duplicate_exact_fact:{fact_id}" for fact_id in duplicate_exact_fact_ids)
        elif exactly_allowlisted_facts:
            grounding_status = "strict_grounded"
        elif lexically_matched_facts:
            grounding_status = "unresolved"
            candidate_issues.append("lexical_match_unresolved")
        else:
            grounding_status = "unsupported"
            candidate_issues.append("unmatched_candidate")
        if "credential" in case.tags and would_persist:
            candidate_issues.append("credential_filter_miss")

        strict_grounded = grounding_status == "strict_grounded"
        if strict_grounded:
            strict_grounded_confidences.append(candidate.confidence)
            for fact_id in exactly_allowlisted_facts:
                seen_strict_fact_ids.add(fact_id)
                raw_strict_fact_matches[fact_id] += 1
                post_filter_strict_fact_matches[fact_id] += 1
        else:
            non_strict_confidences.append(candidate.confidence)

        observation = {
            "index": index,
            "scope": candidate.scope,
            "confidence": candidate.confidence,
            "grounding_status": grounding_status,
            "strict_grounded": strict_grounded,
            "would_persist_pending": would_persist,
            "lexically_matched_fact_ids": lexically_matched_facts,
            "exactly_allowlisted_fact_ids": exactly_allowlisted_facts,
            "issues": candidate_issues,
        }
        if show_output:
            observation["content"] = (
                "[FILTERED SENSITIVE CANDIDATE]" if sensitive else redact_sensitive_text(candidate.content)
            )
        candidate_observations.append(observation)
        issues.extend(f"candidate_{index + 1}:{issue}" for issue in candidate_issues)

    parser_duplicate_count = max(0, int(parser_deduplicated_exact_candidate_count or 0))
    if parser_duplicate_count:
        issues.append(f"parser_deduplicated_exact_candidates:{parser_duplicate_count}")
    missing_raw_lexical = [fact_id for fact_id, count in raw_lexical_fact_matches.items() if count == 0]
    missing_post_filter_lexical = [fact_id for fact_id, count in post_filter_lexical_fact_matches.items() if count == 0]
    missing_raw_strict = [fact_id for fact_id, count in raw_strict_fact_matches.items() if count == 0]
    missing_post_filter_strict = [fact_id for fact_id, count in post_filter_strict_fact_matches.items() if count == 0]
    issues.extend(f"missing_strict_grounding:{fact_id}" for fact_id in missing_raw_strict)

    raw_strict_count = sum(item["strict_grounded"] for item in candidate_observations)
    post_filter_strict_count = sum(
        item["strict_grounded"] and item["would_persist_pending"] for item in candidate_observations
    )
    raw_strict_expectation_observed = (
        not missing_raw_strict and parser_duplicate_count == 0 and raw_strict_count == len(candidate_observations)
    )
    post_filter_strict_expectation_observed = not missing_post_filter_strict and post_filter_strict_count == sum(
        item["would_persist_pending"] for item in candidate_observations
    )
    if case.expectation == "abstain":
        raw_strict_expectation_observed = not candidate_observations
        post_filter_strict_expectation_observed = not any(
            item["would_persist_pending"] for item in candidate_observations
        )

    scope_counts = {scope: 0 for scope in ("global", "workspace", "persona", "chat")}
    for candidate in output.candidates:
        scope_counts[candidate.scope] += 1
    grounding_status_counts = {
        status: sum(item["grounding_status"] == status for item in candidate_observations)
        for status in (
            "strict_grounded",
            "unresolved",
            "contradicted",
            "unsupported",
            "prohibited",
            "duplicate",
        )
    }
    grounding_status_counts["duplicate"] += parser_duplicate_count
    result = {
        "case_id": case.id,
        "tags": list(case.tags),
        "expectation": case.expectation,
        "execution_complete": True,
        "raw_strict_expectation_observed": raw_strict_expectation_observed,
        "post_filter_strict_expectation_observed": post_filter_strict_expectation_observed,
        "raw_candidate_count": len(candidate_observations) + parser_duplicate_count,
        "parsed_candidate_count": len(candidate_observations),
        "parser_deduplicated_exact_candidate_count": parser_duplicate_count,
        "post_filter_candidate_count": sum(item["would_persist_pending"] for item in candidate_observations),
        "filtered_sensitive_count": sum(not item["would_persist_pending"] for item in candidate_observations),
        "raw_strict_grounded_candidate_count": raw_strict_count,
        "post_filter_strict_grounded_candidate_count": post_filter_strict_count,
        "raw_non_strict_candidate_count": len(candidate_observations) - raw_strict_count + parser_duplicate_count,
        "post_filter_non_strict_candidate_count": sum(
            not item["strict_grounded"] and item["would_persist_pending"] for item in candidate_observations
        ),
        "grounding_status_counts": grounding_status_counts,
        "expected_fact_count": len(case.facts),
        "raw_lexically_matched_fact_count": len(case.facts) - len(missing_raw_lexical),
        "post_filter_lexically_matched_fact_count": len(case.facts) - len(missing_post_filter_lexical),
        "raw_strict_grounded_fact_count": len(case.facts) - len(missing_raw_strict),
        "post_filter_strict_grounded_fact_count": len(case.facts) - len(missing_post_filter_strict),
        "missing_raw_lexical_fact_ids": missing_raw_lexical,
        "missing_post_filter_lexical_fact_ids": missing_post_filter_lexical,
        "missing_raw_strict_grounding_fact_ids": missing_raw_strict,
        "missing_post_filter_strict_grounding_fact_ids": missing_post_filter_strict,
        "issues": issues,
        "scope_counts": scope_counts,
        "strict_grounded_confidence": _confidence_summary(strict_grounded_confidences),
        "non_strict_confidence": _confidence_summary(non_strict_confidences),
        "candidate_observations": candidate_observations,
    }
    if show_output:
        result["user_text"] = (
            "[SYNTHETIC CREDENTIAL CASE INPUT HIDDEN]"
            if sensitive_filter(case.user_text)
            else redact_sensitive_text(case.user_text)
        )
    return result


def contract_failure_result(case: MemoryBaselineCase, failure: str, latency_ms: int) -> dict:
    """Return a content-free result for provider or parser failures."""

    safe_failure = redact_sensitive_text(failure or "")
    safe_failure = re.sub(r"https?://[^\s]+", "[ENDPOINT]", safe_failure)[:1000]
    safe_failure = safe_failure or "Task model evaluation failed."
    return {
        "case_id": case.id,
        "tags": list(case.tags),
        "expectation": case.expectation,
        "execution_complete": False,
        "raw_strict_expectation_observed": False,
        "post_filter_strict_expectation_observed": False,
        "contract_error": safe_failure,
        "latency_ms": latency_ms,
        "raw_candidate_count": 0,
        "parsed_candidate_count": 0,
        "parser_deduplicated_exact_candidate_count": 0,
        "post_filter_candidate_count": 0,
        "filtered_sensitive_count": 0,
        "raw_strict_grounded_candidate_count": 0,
        "post_filter_strict_grounded_candidate_count": 0,
        "raw_non_strict_candidate_count": 0,
        "post_filter_non_strict_candidate_count": 0,
        "grounding_status_counts": {
            status: 0
            for status in (
                "strict_grounded",
                "unresolved",
                "contradicted",
                "unsupported",
                "prohibited",
                "duplicate",
            )
        },
        "expected_fact_count": len(case.facts),
        "raw_lexically_matched_fact_count": 0,
        "post_filter_lexically_matched_fact_count": 0,
        "raw_strict_grounded_fact_count": 0,
        "post_filter_strict_grounded_fact_count": 0,
        "missing_raw_lexical_fact_ids": [fact.id for fact in case.facts],
        "missing_post_filter_lexical_fact_ids": [fact.id for fact in case.facts],
        "missing_raw_strict_grounding_fact_ids": [fact.id for fact in case.facts],
        "missing_post_filter_strict_grounding_fact_ids": [fact.id for fact in case.facts],
        "issues": ["contract_error"],
        "scope_counts": {scope: 0 for scope in ("global", "workspace", "persona", "chat")},
        "strict_grounded_confidence": _confidence_summary([]),
        "non_strict_confidence": _confidence_summary([]),
        "candidate_observations": [],
    }


def _ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 6) if denominator else None,
    }


def _merged_confidence(results: list[dict], key: str) -> dict:
    weighted_total = 0.0
    count = 0
    minimum = None
    maximum = None
    for result in results:
        summary = result.get(key) or {}
        observed = int(summary.get("count") or 0)
        if not observed:
            continue
        weighted_total += float(summary["mean"]) * observed
        count += observed
        minimum = summary["minimum"] if minimum is None else min(minimum, summary["minimum"])
        maximum = summary["maximum"] if maximum is None else max(maximum, summary["maximum"])
    return {
        "count": count,
        "minimum": minimum,
        "maximum": maximum,
        "mean": round(weighted_total / count, 6) if count else None,
    }


def build_memory_baseline_report(
    corpus: MemoryBaselineCorpus,
    results: list[dict],
    *,
    model: str,
    base_url: str,
    timeout_seconds: float = 90.0,
) -> dict:
    """Aggregate content-free Memory v2 observations without declaring a quality gate."""

    expected_ids = [case.id for case in corpus.cases]
    observed_ids = [result.get("case_id") for result in results]
    if observed_ids != expected_ids:
        raise ValueError("memory baseline results do not match corpus order")

    raw_candidates = sum(int(result.get("raw_candidate_count") or 0) for result in results)
    post_filter_candidates = sum(int(result.get("post_filter_candidate_count") or 0) for result in results)
    raw_strict_grounded = sum(int(result.get("raw_strict_grounded_candidate_count") or 0) for result in results)
    post_filter_strict_grounded = sum(
        int(result.get("post_filter_strict_grounded_candidate_count") or 0) for result in results
    )
    expected_facts = sum(int(result.get("expected_fact_count") or 0) for result in results)
    raw_lexical_facts = sum(int(result.get("raw_lexically_matched_fact_count") or 0) for result in results)
    post_filter_lexical_facts = sum(
        int(result.get("post_filter_lexically_matched_fact_count") or 0) for result in results
    )
    raw_strict_facts = sum(int(result.get("raw_strict_grounded_fact_count") or 0) for result in results)
    post_filter_strict_facts = sum(int(result.get("post_filter_strict_grounded_fact_count") or 0) for result in results)
    abstain_results = [result for result in results if result.get("expectation") == "abstain"]
    scope_counts = {scope: 0 for scope in ("global", "workspace", "persona", "chat")}
    for result in results:
        for scope in scope_counts:
            scope_counts[scope] += int((result.get("scope_counts") or {}).get(scope) or 0)

    category_results = {}
    for tag in sorted({tag for case in corpus.cases for tag in case.tags}):
        tagged = [result for result in results if tag in (result.get("tags") or [])]
        category_results[tag] = {
            "case_count": len(tagged),
            "raw_strict_expectation_observation_rate": _ratio(
                sum(bool(item.get("raw_strict_expectation_observed")) for item in tagged),
                len(tagged),
            ),
            "post_filter_strict_expectation_observation_rate": _ratio(
                sum(bool(item.get("post_filter_strict_expectation_observed")) for item in tagged),
                len(tagged),
            ),
        }

    contract_error_count = sum(bool(result.get("contract_error")) for result in results)
    payload = {
        "schema_version": BASELINE_REPORT_SCHEMA_VERSION,
        "suite": "memory-v2-baseline",
        "mode": "memory_v2_observe_only",
        "automatic_activation_enabled": False,
        "quality_gate": None,
        "execution_complete": contract_error_count == 0,
        "provider": {
            "name": "ollama",
            "version": {"available": False, "value": None},
        },
        "model": {
            "name": model,
            "digest": {"available": False, "value": None},
        },
        "endpoint": safe_endpoint_descriptor(base_url),
        "reproducibility": baseline_reproducibility(timeout_seconds=timeout_seconds),
        "corpus": {
            "id": corpus.corpus_id,
            "version": corpus.corpus_version,
            "schema_version": corpus.schema_version,
            "sha256": corpus.sha256,
            "synthetic": True,
            "case_count": len(corpus.cases),
        },
        "quality_observations": {
            "all_raw_strict_expectations_observed": all(
                bool(result.get("raw_strict_expectation_observed")) for result in results
            ),
            "all_post_filter_strict_expectations_observed": all(
                bool(result.get("post_filter_strict_expectation_observed")) for result in results
            ),
        },
        "summary": {
            "contract_error_count": contract_error_count,
            "raw_candidate_count": raw_candidates,
            "parsed_candidate_count": sum(int(result.get("parsed_candidate_count") or 0) for result in results),
            "parser_deduplicated_exact_candidate_count": sum(
                int(result.get("parser_deduplicated_exact_candidate_count") or 0) for result in results
            ),
            "post_filter_candidate_count": post_filter_candidates,
            "filtered_sensitive_count": sum(int(result.get("filtered_sensitive_count") or 0) for result in results),
            "raw_strict_grounding_precision_lower_bound": _ratio(raw_strict_grounded, raw_candidates),
            "post_filter_strict_grounding_precision_lower_bound": _ratio(
                post_filter_strict_grounded,
                post_filter_candidates,
            ),
            "raw_non_strict_candidate_rate": _ratio(
                raw_candidates - raw_strict_grounded,
                raw_candidates,
            ),
            "unwanted_pending_rate_after_current_filter": _ratio(
                post_filter_candidates - post_filter_strict_grounded,
                post_filter_candidates,
            ),
            "raw_lexical_expected_fact_match_rate": _ratio(raw_lexical_facts, expected_facts),
            "post_filter_lexical_expected_fact_match_rate": _ratio(
                post_filter_lexical_facts,
                expected_facts,
            ),
            "raw_strict_grounded_expected_fact_coverage": _ratio(raw_strict_facts, expected_facts),
            "post_filter_strict_grounded_expected_fact_coverage": _ratio(
                post_filter_strict_facts,
                expected_facts,
            ),
            "raw_abstention_observation_rate": _ratio(
                sum(bool(result.get("raw_strict_expectation_observed")) for result in abstain_results),
                len(abstain_results),
            ),
            "post_filter_abstention_observation_rate": _ratio(
                sum(bool(result.get("post_filter_strict_expectation_observed")) for result in abstain_results),
                len(abstain_results),
            ),
            "scope_distribution": scope_counts,
            "global_scope_rate": _ratio(scope_counts["global"], raw_candidates),
            "strict_grounded_confidence": _merged_confidence(results, "strict_grounded_confidence"),
            "non_strict_confidence": _merged_confidence(results, "non_strict_confidence"),
        },
        "categories": category_results,
        "results": results,
    }
    return payload


def example_output(case: MemoryBaselineCase, *, scope: str = "persona", confidence: float = 0.9):
    """Build a deterministic known-good parsed output for offline tests."""

    if case.expectation == "abstain":
        return MemoryExtractionTaskOutput(tuple())
    return MemoryExtractionTaskOutput(tuple(MemoryCandidate(fact.example, scope, confidence) for fact in case.facts))
