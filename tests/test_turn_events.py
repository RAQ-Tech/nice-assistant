import unittest

from app.turn_events import TurnEventBroker


class TurnEventBrokerTests(unittest.TestCase):
    def test_snapshot_replay_last_event_and_terminal(self):
        broker = TurnEventBroker(max_events=4, max_bytes=1000)
        first = broker.publish("turn", "turn.queued", {"status": "queued"})
        second = broker.publish("turn", "assistant.delta", {"text": "hello"})
        broker.publish("turn", "turn.completed", {"status": "completed"})
        events = list(broker.subscribe("turn", {"status": "completed"}, last_event_id=first.sequence))
        self.assertEqual(events[0].event, "turn.snapshot")
        self.assertEqual([event.event for event in events[1:]], ["assistant.delta", "turn.completed"])
        self.assertEqual(events[1].sequence, second.sequence)
        self.assertEqual(broker.accumulated_text("turn"), "hello")

    def test_bounded_replay_drops_old_events(self):
        broker = TurnEventBroker(max_events=2, max_bytes=1000)
        broker.publish("turn", "turn.queued", {})
        broker.publish("turn", "assistant.delta", {"text": "a"})
        broker.publish("turn", "assistant.delta", {"text": "b"})
        broker.publish("turn", "turn.completed", {})
        events = list(broker.subscribe("turn", {"status": "completed"}))
        self.assertEqual([event.event for event in events[1:]], ["assistant.delta", "turn.completed"])
        self.assertEqual(broker.accumulated_text("turn"), "ab")

    def test_terminal_replay_expires_while_a_durable_snapshot_can_be_supplied_again(self):
        broker = TurnEventBroker(retention_seconds=0)
        broker.publish("turn", "assistant.delta", {"text": "ephemeral"})
        broker.publish("turn", "turn.completed", {"status": "completed"})
        self.assertEqual(broker.accumulated_text("turn"), "")
        snapshot = {"status": "completed", "accumulated_text": "durable"}
        events = list(broker.subscribe("turn", snapshot))
        self.assertEqual(events[0].event, "turn.snapshot")
        self.assertEqual(events[0].data["accumulated_text"], "durable")


if __name__ == "__main__":
    unittest.main()
