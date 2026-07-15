import unittest

from app.task_contracts import task_definition
from scripts.evaluate_task_models import assess_case, evaluation_cases


VALID_OUTPUTS = {
    "specific_title": '{"title":"Vegetable Garden Irrigation"}',
    "summary_retains_corrections": '{"summary":"The trip is in December. The venue remains undecided."}',
    "memory_extracts_stable_preferences": (
        '{"candidates":[{"content":"The user lives in Portland, Maine.","scope":"global","confidence":0.95}]}'
    ),
    "memory_excludes_credentials": '{"candidates":[]}',
    "capability_skips_ordinary_text": '{"requests":[]}',
    "capability_skips_literal_reply_contract": '{"requests":[]}',
    "capability_skips_literal_outage_reply": '{"requests":[]}',
    "capability_requests_semantic_image": (
        '{"requests":[{"capability_key":"media.generate_image","prompt":"A lighthouse in a storm",'
        '"operation":"generate","domains":[],"content_tags":[],"required_features":[]}]}'
    ),
}


class TaskModelEvaluationTests(unittest.TestCase):
    def test_curated_contract_cases_have_deterministic_acceptance_checks(self):
        cases = evaluation_cases()
        self.assertEqual(len(cases), 8)
        for case in cases:
            with self.subTest(case=case.name):
                definition = task_definition(case.role)
                output = definition.parse_output(
                    VALID_OUTPUTS[case.name],
                    case.task_input,
                    definition.default_max_output_tokens,
                )
                self.assertEqual(assess_case(case, output), [])

    def test_semantic_check_rejects_a_generic_title(self):
        case = evaluation_cases()[0]
        definition = task_definition(case.role)
        output = definition.parse_output('{"title":"Conversation"}', case.task_input, 64)
        self.assertEqual(assess_case(case, output), ["title is generic"])


if __name__ == "__main__":
    unittest.main()
