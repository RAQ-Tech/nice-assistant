from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from app.task_contracts import MemoryCandidate, MemoryExtractionTaskOutput
from scripts.evaluate_task_models import main as evaluation_main
from scripts.evaluate_task_models import run_memory_baseline_case
from scripts.memory_extraction_baseline import (
    BASELINE_CANDIDATE_LIMIT,
    BASELINE_CORPUS_PATH,
    BASELINE_EVALUATOR_VERSION,
    REQUIRED_BASELINE_TAGS,
    assess_memory_baseline_case,
    baseline_reproducibility,
    build_memory_baseline_report,
    contract_failure_result,
    example_output,
    load_memory_baseline_corpus,
)


class FakeBaselineProvider:
    def __init__(self, output):
        self.output = output
        self.requests = []

    def generate(self, request, _cancellation):
        self.requests.append(request)
        return self.output


class FakeBaselineCliProvider:
    def __init__(self, corpus, *, empty_case_id=None, invalid_case_id=None):
        self.cases = {case.user_text: case for case in corpus.cases}
        self.empty_case_id = empty_case_id
        self.invalid_case_id = invalid_case_id
        self.requests = []

    def list_models(self):
        return ["synthetic-test-model"]

    def generate(self, request, _cancellation):
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        case = self.cases[payload["user_text"]]
        if case.id == self.invalid_case_id:
            return "invalid JSON"
        output = MemoryExtractionTaskOutput(tuple()) if case.id == self.empty_case_id else example_output(case)
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": candidate.content,
                        "confidence": candidate.confidence,
                    }
                    for candidate in output.candidates
                ]
            }
        )


class MemoryExtractionBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_memory_baseline_corpus()
        cls.cases = {case.id: case for case in cls.corpus.cases}

    def _result(self, case_id, content, *, scope="persona", confidence=0.9, **kwargs):
        return assess_memory_baseline_case(
            self.corpus,
            self.cases[case_id],
            MemoryExtractionTaskOutput((MemoryCandidate(content, confidence),)),
            evaluation_scope=scope,
            **kwargs,
        )

    def _report(self, results, *, base_url="http://127.0.0.1:11434", timeout_seconds=90.0):
        return build_memory_baseline_report(
            self.corpus,
            results,
            model="synthetic-test-model",
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def test_committed_corpus_is_versioned_synthetic_and_covers_required_categories(self):
        self.assertEqual(self.corpus.corpus_id, "memory-v2-extraction-baseline")
        self.assertEqual(self.corpus.corpus_version, "1.1.0")
        self.assertEqual(len(self.corpus.cases), 38)
        self.assertEqual(len(self.corpus.sha256), 64)
        self.assertEqual(
            REQUIRED_BASELINE_TAGS - {tag for case in self.corpus.cases for tag in case.tags},
            set(),
        )
        self.assertIn("Synthetic, privacy-safe", self.corpus.description)
        self.assertEqual(len({case.id for case in self.corpus.cases}), len(self.corpus.cases))
        fact_ids = [fact.id for case in self.corpus.cases for fact in case.facts]
        self.assertEqual(len(set(fact_ids)), len(fact_ids))

    def test_loader_rejects_non_synthetic_or_duplicate_case_data(self):
        payload = json.loads(BASELINE_CORPUS_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            payload["synthetic"] = False
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicitly synthetic"):
                load_memory_baseline_corpus(path)

            payload["synthetic"] = True
            payload["cases"][1]["id"] = payload["cases"][0]["id"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate memory baseline case id"):
                load_memory_baseline_corpus(path)

    def test_exact_allowlisted_outputs_meet_the_strict_grounding_lower_bound(self):
        for case in self.corpus.cases:
            with self.subTest(case=case.id):
                result = assess_memory_baseline_case(self.corpus, case, example_output(case))
                self.assertTrue(result["execution_complete"], result)
                self.assertTrue(result["raw_strict_expectation_observed"], result)
                self.assertTrue(result["post_filter_strict_expectation_observed"], result)

    def test_lexical_paraphrase_is_reported_separately_and_never_strict_grounding(self):
        result = self._result(
            "stable_preference_metric",
            "The user has a preference for metric units.",
        )
        observation = result["candidate_observations"][0]
        self.assertEqual(observation["grounding_status"], "unresolved")
        self.assertFalse(observation["strict_grounded"])
        self.assertEqual(observation["lexically_matched_fact_ids"], ["fact_metric_preference"])
        self.assertEqual(result["raw_lexically_matched_fact_count"], 1)
        self.assertEqual(result["raw_strict_grounded_fact_count"], 0)
        self.assertFalse(result["raw_strict_expectation_observed"])

    def test_duplicate_exact_candidates_are_diagnosed_and_do_not_look_strictly_clean(self):
        case = self.cases["stable_preference_metric"]
        candidate = MemoryCandidate("The user prefers metric units.", 0.9)
        result = assess_memory_baseline_case(
            self.corpus,
            case,
            MemoryExtractionTaskOutput((candidate, candidate)),
        )
        self.assertFalse(result["raw_strict_expectation_observed"])
        self.assertEqual(result["raw_strict_grounded_candidate_count"], 1)
        self.assertEqual(result["raw_non_strict_candidate_count"], 1)
        self.assertEqual(result["grounding_status_counts"]["duplicate"], 1)
        self.assertIn("candidate_2:duplicate_exact_fact:fact_metric_preference", result["issues"])

    def test_runner_reports_exact_duplicates_removed_by_the_task_parser(self):
        content = "The user prefers metric units."
        raw = json.dumps(
            {
                "candidates": [
                    {"content": content, "confidence": 0.9},
                    {"content": content, "confidence": 0.9},
                ]
            }
        )
        result = run_memory_baseline_case(
            FakeBaselineProvider(raw),
            "synthetic-test-model",
            self.corpus,
            self.cases["stable_preference_metric"],
            1.0,
            False,
        )
        self.assertTrue(result["execution_complete"])
        self.assertFalse(result["raw_strict_expectation_observed"])
        self.assertTrue(result["post_filter_strict_expectation_observed"])
        self.assertEqual(result["raw_candidate_count"], 2)
        self.assertEqual(result["parsed_candidate_count"], 1)
        self.assertEqual(result["parser_deduplicated_exact_candidate_count"], 1)
        self.assertEqual(result["grounding_status_counts"]["duplicate"], 1)

    def test_contradictions_and_arbitrary_extra_claims_never_count_as_strict_grounding(self):
        cases = [
            ("stable_preference_metric", "The user does not prefer metric units."),
            ("stable_relationship_pet", "The user's cat is not named Pixel."),
            ("negation_self_contained_correction", "The user's favorite color is blue, not teal."),
            ("stable_preference_metric", "The user prefers metric units and lives in Boston."),
        ]
        for case_id, content in cases:
            with self.subTest(case=case_id, content=content):
                result = self._result(case_id, content)
                observation = result["candidate_observations"][0]
                self.assertFalse(observation["strict_grounded"], result)
                self.assertEqual(result["raw_strict_grounded_candidate_count"], 0)
                self.assertFalse(result["raw_strict_expectation_observed"])

    def test_questions_vague_text_and_bare_acceptance_require_abstention(self):
        examples = {
            "question_pure_preference": "The user prefers tea.",
            "vague_deictic_preference": "The user prefers the second option.",
            "acceptance_bare_assent": "The user accepted quarterly billing.",
        }
        for case_id, content in examples.items():
            with self.subTest(case=case_id):
                result = self._result(case_id, content, scope="global", confidence=0.99)
                self.assertFalse(result["raw_strict_expectation_observed"])
                self.assertFalse(result["post_filter_strict_expectation_observed"])
                self.assertEqual(result["grounding_status_counts"]["unsupported"], 1)
                self.assertIn("candidate_1:unexpected_candidate", result["issues"])
                self.assertNotIn("user_text", result)
                self.assertNotIn("content", result["candidate_observations"][0])

    def test_case_forbidden_claims_are_contradicted_not_grounded(self):
        examples = {
            "negation_simple_behavior": "The user drinks coffee.",
            "negation_subject_contrast": "The user owns a dog; the sister does not own a dog.",
            "negation_self_contained_correction": "The user's favorite color is blue.",
            "acceptance_rejected_decision": "Project Juniper will use quarterly billing.",
        }
        for case_id, content in examples.items():
            with self.subTest(case=case_id):
                result = self._result(case_id, content, confidence=0.99)
                self.assertEqual(result["grounding_status_counts"]["contradicted"], 1)
                self.assertEqual(result["raw_strict_grounded_candidate_count"], 0)
                self.assertTrue(any("forbidden_claim" in issue for issue in result["issues"]), result)

    def test_invented_synthetic_value_is_contradicted_even_with_a_lexical_match(self):
        result = self._result(
            "stable_preference_metric",
            "The user prefers metric units and lives in Oslo.",
            scope="global",
            confidence=0.98,
        )
        observation = result["candidate_observations"][0]
        self.assertEqual(observation["grounding_status"], "contradicted")
        self.assertFalse(observation["strict_grounded"])
        self.assertIn("candidate_1:invented_value:oslo", result["issues"])
        self.assertEqual(result["raw_lexically_matched_fact_count"], 1)
        self.assertEqual(result["raw_strict_grounded_fact_count"], 0)

    def test_confidence_and_evaluation_scope_metadata_are_diagnostic_only(self):
        content = "The user prefers metric units."
        low = self._result("stable_preference_metric", content, scope="chat", confidence=0.0)
        high = self._result("stable_preference_metric", content, scope="global", confidence=1.0)
        self.assertTrue(low["raw_strict_expectation_observed"])
        self.assertTrue(high["raw_strict_expectation_observed"])
        self.assertEqual(
            low["raw_strict_grounded_fact_count"],
            high["raw_strict_grounded_fact_count"],
        )
        self.assertEqual(low["scope_counts"]["chat"], 1)
        self.assertEqual(high["scope_counts"]["global"], 1)
        self.assertEqual(low["scope_assignment_source"], "evaluation_metadata_not_runtime_output")
        self.assertEqual(high["scope_assignment_source"], "evaluation_metadata_not_runtime_output")
        self.assertEqual(low["strict_grounded_confidence"]["mean"], 0.0)
        self.assertEqual(high["strict_grounded_confidence"]["mean"], 1.0)

    def test_sensitivity_is_decided_before_strict_grounding(self):
        result = self._result(
            "stable_preference_metric",
            "The user prefers metric units.",
            sensitive_filter=lambda _content: True,
        )
        observation = result["candidate_observations"][0]
        self.assertEqual(observation["grounding_status"], "prohibited")
        self.assertFalse(observation["strict_grounded"])
        self.assertEqual(result["raw_strict_grounded_candidate_count"], 0)
        self.assertEqual(result["raw_lexically_matched_fact_count"], 1)
        self.assertFalse(result["raw_strict_expectation_observed"])

    def test_sensitive_output_separates_raw_extractor_failure_and_current_filter_safety(self):
        content = "The user's temporary API key is sk-not-a-real-evaluation-secret."
        result = self._result(
            "credential_api_key",
            content,
            scope="global",
            confidence=0.99,
            show_output=True,
        )
        self.assertFalse(result["raw_strict_expectation_observed"])
        self.assertTrue(result["post_filter_strict_expectation_observed"])
        self.assertEqual(result["raw_strict_grounded_candidate_count"], 0)
        self.assertEqual(result["grounding_status_counts"]["prohibited"], 1)
        self.assertEqual(result["post_filter_candidate_count"], 0)
        self.assertEqual(result["filtered_sensitive_count"], 1)
        self.assertEqual(
            result["candidate_observations"][0]["content"],
            "[FILTERED SENSITIVE CANDIDATE]",
        )
        self.assertNotIn("sk-not-a-real-evaluation-secret", json.dumps(result))

    def test_credential_filter_miss_remains_an_unwanted_pending_candidate(self):
        result = self._result(
            "credential_password",
            "The user stores TEST_ONLY_DO_NOT_USE_0002.",
            scope="chat",
            confidence=0.8,
        )
        self.assertFalse(result["raw_strict_expectation_observed"])
        self.assertFalse(result["post_filter_strict_expectation_observed"])
        self.assertEqual(result["post_filter_candidate_count"], 1)
        self.assertEqual(result["post_filter_non_strict_candidate_count"], 1)
        self.assertIn("candidate_1:credential_filter_miss", result["issues"])

    def test_report_is_observe_only_content_free_and_uses_strict_lower_bound_names(self):
        results = [assess_memory_baseline_case(self.corpus, case, example_output(case)) for case in self.corpus.cases]
        endpoint = "http://" + "test-user:test-secret@" + "127.0.0.1:11434/private-path?token=test-only"
        report = self._report(results, base_url=endpoint, timeout_seconds=17.0)
        serialized = json.dumps(report)
        self.assertTrue(report["execution_complete"])
        self.assertNotIn("passed", report)
        self.assertNotIn("post_filter_passed", report)
        self.assertEqual(report["mode"], "memory_v2_observe_only")
        self.assertFalse(report["automatic_activation_enabled"])
        self.assertIsNone(report["quality_gate"])
        self.assertEqual(report["endpoint"]["identifier"], {"available": False, "value": None})
        self.assertEqual(report["reproducibility"]["execution_options"]["timeout_seconds"], 17.0)
        self.assertTrue(report["quality_observations"]["all_raw_strict_expectations_observed"])
        self.assertEqual(
            report["summary"]["raw_strict_grounding_precision_lower_bound"]["value"],
            1.0,
        )
        self.assertEqual(
            report["summary"]["raw_lexical_expected_fact_match_rate"]["value"],
            1.0,
        )
        self.assertEqual(
            report["summary"]["raw_strict_grounded_expected_fact_coverage"]["value"],
            1.0,
        )
        self.assertNotIn(endpoint, serialized)
        self.assertNotIn("test-user", serialized)
        self.assertNotIn("test-secret", serialized)
        self.assertNotIn("private-path", serialized)
        self.assertNotIn("I prefer metric units.", serialized)
        self.assertNotIn('"content"', serialized)
        self.assertNotIn('"user_text"', serialized)

    def test_report_reproducibility_hashes_actual_local_contract_and_marks_unknown_metadata(self):
        first = baseline_reproducibility()
        second = baseline_reproducibility()
        self.assertEqual(first, second)
        self.assertEqual(first["evaluator_version"], BASELINE_EVALUATOR_VERSION)
        self.assertEqual(first["candidate_limit"], BASELINE_CANDIDATE_LIMIT)
        self.assertEqual(first["generation_options"], {"num_predict": 384, "temperature": 0.0})
        for value in first["contract_hashes"].values():
            self.assertEqual(len(value), 64)
        for value in first["source_hashes"].values():
            self.assertEqual(len(value), 64)

        report = self._report(
            [assess_memory_baseline_case(self.corpus, case, example_output(case)) for case in self.corpus.cases]
        )
        self.assertFalse(report["provider"]["version"]["available"])
        self.assertIsNone(report["provider"]["version"]["value"])
        self.assertFalse(report["model"]["digest"]["available"])
        self.assertIsNone(report["model"]["digest"]["value"])
        self.assertEqual(report["reproducibility"], first)

    def test_detailed_output_is_explicit_and_sensitive_content_remains_hidden(self):
        stable = assess_memory_baseline_case(
            self.corpus,
            self.cases["stable_preference_metric"],
            example_output(self.cases["stable_preference_metric"]),
            show_output=True,
        )
        self.assertEqual(stable["user_text"], "I prefer metric units.")
        self.assertEqual(stable["candidate_observations"][0]["content"], "The user prefers metric units.")

        credential = self._result(
            "credential_password",
            "The user's password is TEST_ONLY_DO_NOT_USE_0002.",
            scope="global",
            show_output=True,
        )
        self.assertEqual(
            credential["candidate_observations"][0]["content"],
            "[FILTERED SENSITIVE CANDIDATE]",
        )

    def test_contract_failures_are_content_free_mark_execution_incomplete_and_hide_endpoints(self):
        case = self.cases["stable_preference_metric"]
        result = contract_failure_result(
            case,
            "ProviderError: request to http://private-host.invalid:11434/path failed",
            12,
        )
        results = [
            result if item.id == case.id else assess_memory_baseline_case(self.corpus, item, example_output(item))
            for item in self.corpus.cases
        ]
        report = self._report(results, base_url="http://private-host.invalid:11434")
        serialized = json.dumps(report)
        self.assertFalse(report["execution_complete"])
        self.assertEqual(report["summary"]["contract_error_count"], 1)
        self.assertNotIn("passed", report)
        self.assertNotIn("private-host.invalid", serialized)
        self.assertIn("[ENDPOINT]", result["contract_error"])
        self.assertNotIn("user_text", result)
        self.assertNotIn("content", result)

    def test_direct_runner_uses_only_the_task_contract_and_pure_sensitive_filter(self):
        case = self.cases["question_pure_preference"]
        provider = FakeBaselineProvider('{"candidates":[]}')
        with mock.patch("app.memory_service.UnitOfWork", side_effect=AssertionError("database access attempted")):
            result = run_memory_baseline_case(
                provider,
                "synthetic-test-model",
                self.corpus,
                case,
                1.0,
                False,
            )
        self.assertTrue(result["execution_complete"])
        self.assertTrue(result["raw_strict_expectation_observed"])
        self.assertEqual(len(provider.requests), 1)
        payload = json.loads(provider.requests[0].messages[-1]["content"])
        self.assertEqual(payload["user_text"], case.user_text)
        self.assertEqual(payload["max_candidates"], BASELINE_CANDIDATE_LIMIT)
        self.assertNotIn("user_text", result)
        self.assertNotIn("content", json.dumps(result))

    def test_cli_quality_misses_are_observations_and_do_not_fail_execution(self):
        provider = FakeBaselineCliProvider(self.corpus, empty_case_id="stable_preference_metric")
        stdout = io.StringIO()
        argv = [
            "evaluate_task_models.py",
            "--suite",
            "memory-v2-baseline",
            "--base-url",
            "http://private-evaluator.lan:11434",
            "--model",
            "synthetic-test-model",
        ]
        with (
            mock.patch("scripts.evaluate_task_models.OllamaChatProvider", return_value=provider),
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
        ):
            exit_code = evaluation_main()
        report = json.loads(stdout.getvalue())
        serialized = json.dumps(report)
        self.assertEqual(exit_code, 0)
        self.assertTrue(report["execution_complete"])
        self.assertFalse(report["quality_observations"]["all_raw_strict_expectations_observed"])
        self.assertNotIn("passed", report)
        self.assertNotIn("private-evaluator.lan", serialized)
        self.assertNotIn('"content"', serialized)
        self.assertNotIn('"user_text"', serialized)

    def test_cli_contract_failure_is_the_only_baseline_nonzero_outcome(self):
        provider = FakeBaselineCliProvider(self.corpus, invalid_case_id="stable_preference_metric")
        stdout = io.StringIO()
        argv = [
            "evaluate_task_models.py",
            "--suite",
            "memory-v2-baseline",
            "--model",
            "synthetic-test-model",
        ]
        with (
            mock.patch("scripts.evaluate_task_models.OllamaChatProvider", return_value=provider),
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
        ):
            exit_code = evaluation_main()
        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(report["execution_complete"])
        self.assertEqual(report["summary"]["contract_error_count"], 1)


if __name__ == "__main__":
    unittest.main()
