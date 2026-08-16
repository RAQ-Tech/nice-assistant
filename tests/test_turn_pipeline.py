"""What a turn decided, and what it tells the model about capabilities.

These were closures over `create_turn`'s locals and could not be tested without
running a whole turn. They are the two facts the extraction is supposed to make
safe: the values a turn resolved cannot change under its own follow-ups, and the
application instructions are derived from what was actually offered.
"""

import dataclasses
import unittest

from app.turn_pipeline import (
    IMAGE_SENDS_DISABLED_INSTRUCTION,
    MEDIA_CLAIM_INSTRUCTION,
    TurnContext,
    TurnPipeline,
)


def context(**overrides) -> TurnContext:
    values = {
        "user_id": "u",
        "chat_id": "c",
        "text": "hello",
        "provider_name": "ollama",
        "model": "demo",
        "memory_mode": "off",
        "workspace_id": "w",
        "persona_id": "p",
        "persona_name": "Avery",
        "persona_instructions": "Be Avery.",
        "example_dialogue": "",
        "owner_profile": "",
        "allow_persona_image_sends": True,
        "explicit_image_request": False,
        "turn_id": "t",
        "job_id": "j",
        "user_message_id": "m",
        "should_generate_title": False,
        "deterministic_title": None,
    }
    values.update(overrides)
    return TurnContext(**values)


class Service:
    """Only the attributes the pipeline reads at construction."""

    providers = context_service = capabilities = task_models = memory = jobs = broker = None
    generation_timeout_seconds = 30

    def __init__(self):
        self.context = None


class TurnContextTests(unittest.TestCase):
    def test_what_a_turn_resolved_cannot_change_afterwards(self):
        ctx = context()

        # Follow-ups run minutes later, on other threads. If they could rewrite
        # what the turn decided, the reply and the work scheduled after it would
        # stop describing the same turn.
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.model = "something-else"

    def test_two_turns_do_not_share_mutable_defaults(self):
        first = context()
        second = context()
        first.lore_entries.append("entry")

        self.assertEqual(second.lore_entries, [])


class ApplicationInstructionTests(unittest.TestCase):
    def _instructions(self, definitions, *, allow_images=True):
        pipeline = TurnPipeline(Service(), context(allow_persona_image_sends=allow_images))
        return pipeline._application_instructions(definitions)

    def test_the_media_claim_guard_is_sent_only_when_something_could_be_planned(self):
        self.assertEqual(self._instructions([]), [])
        self.assertIn(MEDIA_CLAIM_INSTRUCTION, self._instructions(["media.generate_image"]))

    def test_a_persona_with_sending_disabled_is_told_to_say_so(self):
        instructions = self._instructions([], allow_images=False)

        self.assertEqual(instructions, [IMAGE_SENDS_DISABLED_INSTRUCTION])

    def test_both_apply_together(self):
        instructions = self._instructions(["media.generate_image"], allow_images=False)

        # Order matters: the platform's rule about claiming comes before the
        # persona-specific one it qualifies.
        self.assertEqual(instructions, [MEDIA_CLAIM_INSTRUCTION, IMAGE_SENDS_DISABLED_INSTRUCTION])


if __name__ == "__main__":
    unittest.main()
