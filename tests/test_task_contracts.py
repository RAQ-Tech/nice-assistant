import unittest

from app.task_contracts import guard_premature_media_completion_claim


class TaskContractTests(unittest.TestCase):
    def test_live_completion_phrasings_are_replaced_until_platform_evidence_exists(self):
        request = "Please generate and send me a picture of a blue mug."
        for reply in (
            "[Image sent] A simple picture is created. I hope you like it!",
            "Image sent. I hope you like it!",
            "Here is your picture. I have verified the identity match.",
            "Here is that picture for you: [Image]",
        ):
            with self.subTest(reply=reply):
                guarded, changed = guard_premature_media_completion_claim(request, reply)
                self.assertTrue(changed)
                self.assertEqual(guarded, "I’ll try to make that picture for you.")

    def test_a_future_intent_can_survive_after_a_false_completion_status_is_removed(self):
        guarded, changed = guard_premature_media_completion_claim(
            "Create a picture of a garden.",
            "I'll make it now. [Picture sent]",
        )

        self.assertTrue(changed)
        self.assertEqual(guarded, "I'll make it now.")


if __name__ == "__main__":
    unittest.main()
