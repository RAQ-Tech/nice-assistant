"""When it is acceptable to make pictures nobody has asked for yet.

Pre-generation spends electricity on a machine someone else is using, so the
decision to start is a small pure function with a stated reason. "Nothing
happened last night" and "it is broken" need different fixes, and an operator
cannot tell them apart without being told which it was. See ADR 0030.
"""

import unittest

from app.pregeneration import PregenerationPolicy, may_produce


def decide(policy: PregenerationPolicy, **overrides):
    values = {
        "hour": 3,
        "interactive_pending": 0,
        "media_pending": 0,
        "media_active": 0,
        "approved_waiting": 2,
    }
    values.update(overrides)
    return may_produce(policy, **values)


class QuietWindowTests(unittest.TestCase):
    def test_a_window_that_wraps_past_midnight_is_the_normal_case(self):
        policy = PregenerationPolicy(enabled=True, start_hour=23, end_hour=5)
        self.assertTrue(policy.window_contains(23))
        self.assertTrue(policy.window_contains(0))
        self.assertTrue(policy.window_contains(4))
        self.assertFalse(policy.window_contains(5))
        self.assertFalse(policy.window_contains(12))

    def test_a_daytime_window_does_not_wrap(self):
        policy = PregenerationPolicy(enabled=True, start_hour=2, end_hour=6)
        self.assertTrue(policy.window_contains(2))
        self.assertFalse(policy.window_contains(1))
        self.assertFalse(policy.window_contains(6))

    def test_an_empty_window_never_matches(self):
        policy = PregenerationPolicy(enabled=True, start_hour=4, end_hour=4)
        self.assertFalse(policy.window_contains(4))


class ProductionDecisionTests(unittest.TestCase):
    def test_it_is_off_unless_someone_switched_it_on(self):
        decision = decide(PregenerationPolicy())
        self.assertFalse(decision.allowed)
        self.assertIn("switched off", decision.reason)

    def test_a_waiting_conversation_outranks_a_picture_nobody_asked_for(self):
        decision = decide(PregenerationPolicy(enabled=True), interactive_pending=1)
        self.assertFalse(decision.allowed)
        self.assertIn("conversation is waiting", decision.reason)

    def test_a_requested_picture_comes_first(self):
        queued = decide(PregenerationPolicy(enabled=True), media_pending=1)
        running = decide(PregenerationPolicy(enabled=True), media_active=1)
        self.assertFalse(queued.allowed)
        self.assertFalse(running.allowed)
        self.assertIn("already queued or running", queued.reason)

    def test_outside_the_window_says_which_window(self):
        decision = decide(PregenerationPolicy(enabled=True, start_hour=2, end_hour=6), hour=14)
        self.assertFalse(decision.allowed)
        self.assertIn("02:00-06:00", decision.reason)

    def test_nothing_approved_is_a_stated_reason_not_silence(self):
        decision = decide(PregenerationPolicy(enabled=True), approved_waiting=0)
        self.assertFalse(decision.allowed)
        self.assertIn("no approved scene", decision.reason)

    def test_quiet_idle_and_approved_is_the_only_way_through(self):
        decision = decide(PregenerationPolicy(enabled=True))
        self.assertTrue(decision.allowed)
        self.assertIn("quiet", decision.reason)


if __name__ == "__main__":
    unittest.main()
