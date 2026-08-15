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
    "memory_ignores_a_transient_request": '{"candidates":[]}',
    "memory_ignores_an_inference": '{"candidates":[]}',
    "memory_rates_a_hedged_statement_below_the_floor": (
        '{"candidates":[{"content":"The user may learn Portuguese.","confidence":0.25}]}'
    ),
    "memory_keeps_an_explicit_durable_fact_confident": (
        '{"candidates":[{"content":"The user is allergic to shellfish.","confidence":0.97}]}'
    ),
    "capability_skips_ordinary_text": '{"requests":[]}',
    "capability_skips_literal_reply_contract": '{"requests":[]}',
    "capability_skips_literal_outage_reply": '{"requests":[]}',
    "capability_skips_story_instruction": '{"requests":[]}',
    "capability_skips_image_discussion": '{"requests":[]}',
    "capability_skips_hypothetical": '{"requests":[]}',
    "capability_skips_quoted_instruction": '{"requests":[]}',
    "capability_requests_semantic_image": (
        '{"requests":[{"capability_key":"media.generate_image","scene":{"subject":"A lighthouse in a storm","action":"","setting":"","wardrobe":"","framing":"","lighting":"","camera":"","mood":""},'
        '"operation":"generate","domains":[],"content_tags":[],"required_features":[],"persona_subject":false}]}'
    ),
}


class TaskModelEvaluationTests(unittest.TestCase):
    def test_curated_contract_cases_have_deterministic_acceptance_checks(self):
        cases = evaluation_cases()
        self.assertEqual(len(cases), 16)
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
