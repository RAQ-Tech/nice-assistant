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

    def test_disabled_persona_image_sends_replace_promises_and_completion_claims(self):
        for reply in (
            "I can create that for you.",
            "I'll make that picture now.",
            "Here is that picture for you: [Image]",
        ):
            with self.subTest(reply=reply):
                guarded, changed = guard_premature_media_completion_claim(
                    "Create a picture of a garden.",
                    reply,
                    image_sends_allowed=False,
                )

                self.assertTrue(changed)
                self.assertEqual(guarded, "Picture sending is turned off for this persona.")

        video_reply, changed = guard_premature_media_completion_claim(
            "Create a short video of a garden.",
            "I'll create that video now.",
            image_sends_allowed=False,
        )
        self.assertFalse(changed)
        self.assertEqual(video_reply, "I'll create that video now.")

        guarded, changed = guard_premature_media_completion_claim(
            "Create an animated portrait image.",
            "I'll make that animated image now.",
            image_sends_allowed=False,
        )
        self.assertTrue(changed)
        self.assertEqual(guarded, "Picture sending is turned off for this persona.")


if __name__ == "__main__":
    unittest.main()
