import unittest

from app.persona_output import PersonaOutputStreamFilter, safe_persona_output_text, sanitize_persona_output


class CompletePersonaOutputTests(unittest.TestCase):
    def test_removes_case_insensitive_complete_and_unclosed_envelopes(self):
        complete = sanitize_persona_output("Before [SyStEm_PrOmPt]private policy[/sYsTeM_pRoMpT] after.")
        unclosed = sanitize_persona_output("Visible.[SYSTEM_PROMPT]private policy")

        self.assertEqual(complete.text, "Before  after.")
        self.assertTrue(complete.protected_content_removed)
        self.assertEqual(unclosed.text, "Visible.")
        self.assertTrue(unclosed.protected_content_removed)

    def test_removes_multiple_envelopes_and_stray_closing_markers(self):
        result = sanitize_persona_output(
            "A[SYSTEM_PROMPT]one[/SYSTEM_PROMPT]B[/SYSTEM_PROMPT]C[system_prompt]two[/system_prompt]D"
        )

        self.assertEqual(result.text, "ABCD")
        self.assertTrue(result.protected_content_removed)

    def test_nested_envelopes_do_not_expose_outer_protected_content(self):
        result = sanitize_persona_output(
            "[SYSTEM_PROMPT]outer [SYSTEM_PROMPT]inner[/SYSTEM_PROMPT] still outer[/SYSTEM_PROMPT]safe"
        )

        self.assertEqual(result.text, "safe")
        self.assertTrue(result.protected_content_removed)

    def test_preserves_ordinary_text_and_incomplete_marker_like_text(self):
        text = "Keep [SYSTEM note], [SYSTEM_PROMPT, and ordinary brackets exactly."

        result = sanitize_persona_output(text)

        self.assertEqual(result.text, text)
        self.assertFalse(result.protected_content_removed)

    def test_complete_legacy_prompt_exposure_becomes_a_safe_fallback(self):
        self.assertEqual(
            safe_persona_output_text("[SYSTEM_PROMPT]private policy[/SYSTEM_PROMPT]"),
            "Sorry, something went wrong with that reply. Please try again.",
        )


class StreamingPersonaOutputTests(unittest.TestCase):
    def test_split_markers_and_protected_content_never_leak(self):
        stream_filter = PersonaOutputStreamFilter()
        emitted: list[str] = []

        for chunk in (
            "Before [SYS",
            "TEM_PRO",
            "MPT]private [text]",
            "[/system_",
            "prompt] after.",
        ):
            result = stream_filter.feed(chunk)
            emitted.append(result.text)
            visible_so_far = "".join(emitted)
            self.assertNotIn("SYSTEM_PROMPT", visible_so_far.upper())
            self.assertNotIn("private", visible_so_far)

        final = stream_filter.finish()
        emitted.append(final.text)

        self.assertEqual("".join(emitted), "Before  after.")
        self.assertTrue(final.protected_content_removed)
        self.assertTrue(stream_filter.protected_content_removed)

    def test_character_at_a_time_stream_matches_complete_sanitization(self):
        text = (
            "First [SYSTEM_PROMPT]do not reveal[/SYSTEM_PROMPT] second [system_prompt]also hidden[/system_prompt] last."
        )
        expected = sanitize_persona_output(text)
        stream_filter = PersonaOutputStreamFilter()
        emitted: list[str] = []

        for character in text:
            result = stream_filter.feed(character)
            emitted.append(result.text)
            self.assertNotIn("[SYSTEM_PROMPT]", "".join(emitted).upper())
            self.assertNotIn("[/SYSTEM_PROMPT]", "".join(emitted).upper())
        emitted.append(stream_filter.finish().text)

        self.assertEqual("".join(emitted), expected.text)
        self.assertTrue(stream_filter.protected_content_removed)

    def test_failed_marker_candidate_is_released_without_changes(self):
        stream_filter = PersonaOutputStreamFilter()

        first = stream_filter.feed("Hello [SYS")
        second = stream_filter.feed("tem-not-a-marker")
        final = stream_filter.finish()

        self.assertEqual(first.text, "Hello ")
        self.assertEqual(second.text + final.text, "[SYStem-not-a-marker")
        self.assertFalse(final.protected_content_removed)

    def test_unclosed_envelope_discards_the_remaining_stream(self):
        stream_filter = PersonaOutputStreamFilter()

        before = stream_filter.feed("Visible [SYSTEM_PROMPT]")
        hidden = stream_filter.feed("secret")
        final = stream_filter.finish()

        self.assertEqual(before.text, "Visible ")
        self.assertEqual(hidden.text, "")
        self.assertEqual(final.text, "")
        self.assertTrue(final.protected_content_removed)

    def test_nested_split_envelopes_remain_protected_until_the_outer_close(self):
        stream_filter = PersonaOutputStreamFilter()
        emitted = []
        for chunk in (
            "[SYSTEM_",
            "PROMPT]outer [SYSTEM_PROMPT]inner[/SYSTEM_",
            "PROMPT] still outer[/SYSTEM_PROMPT]",
            "safe",
        ):
            emitted.append(stream_filter.feed(chunk).text)
        emitted.append(stream_filter.finish().text)

        self.assertEqual("".join(emitted), "safe")
        self.assertTrue(stream_filter.protected_content_removed)

    def test_feed_after_finish_is_rejected(self):
        stream_filter = PersonaOutputStreamFilter()
        stream_filter.finish()

        with self.assertRaisesRegex(RuntimeError, "already finished"):
            stream_filter.feed("late")


if __name__ == "__main__":
    unittest.main()
