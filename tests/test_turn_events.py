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

    def test_reading_finished_turns_leaves_nothing_behind(self):
        # Every page that opens an old chat reads its turns. Each read used to
        # leave an empty stream that never ended, so it was never pruned.
        broker = TurnEventBroker()
        for index in range(1000):
            turn_id = f"old-{index}"
            self.assertEqual(broker.snapshot_state(turn_id), ("", 0))
            self.assertEqual(broker.accumulated_text(turn_id), "")
            events = list(broker.subscribe(turn_id, {"status": "completed"}))
            self.assertEqual([event.event for event in events], ["turn.snapshot"])
        self.assertEqual(broker._streams, {})

    def test_a_subscriber_can_wait_for_a_turn_that_has_not_published_yet(self):
        broker = TurnEventBroker()
        stream = broker.subscribe("queued", {"status": "queued"})
        self.assertEqual(next(stream).event, "turn.snapshot")
        broker.publish("queued", "assistant.delta", {"text": "hi"})
        broker.publish("queued", "turn.completed", {})
        self.assertEqual([event.event for event in stream if event], ["assistant.delta", "turn.completed"])


if __name__ == "__main__":
    unittest.main()


class ReconnectReplayTests(unittest.TestCase):
    """A subscriber applies the snapshot as authoritative text and appends deltas after it.
    The snapshot and the replay therefore have to agree on one cursor."""

    def _client_text(self, broker, turn_id, last_event_id, after=()):
        text, cursor = broker.snapshot_state(turn_id)
        snapshot = {"status": "running", "accumulated_text": text, "event_cursor": cursor}
        stream = broker.subscribe(turn_id, snapshot, last_event_id)
        rendered = next(stream).data["accumulated_text"]
        for event, data in after:
            broker.publish(turn_id, event, data)
        broker.publish(turn_id, "turn.completed", {})
        for event in stream:
            if event is not None and event.event == "assistant.delta":
                rendered += event.data["text"]
        return rendered

    def test_a_mid_reply_reconnect_does_not_duplicate_what_it_already_has(self):
        broker = TurnEventBroker()
        for index in range(6):
            broker.publish("turn", "assistant.delta", {"text": f"w{index} "})
        self.assertEqual(self._client_text(broker, "turn", last_event_id=2), "w0 w1 w2 w3 w4 w5 ")

    def test_deltas_produced_after_the_snapshot_still_arrive(self):
        broker = TurnEventBroker()
        for index in range(3):
            broker.publish("turn", "assistant.delta", {"text": f"w{index} "})
        rendered = self._client_text(broker, "turn", last_event_id=1, after=[("assistant.delta", {"text": "w3 "})])
        self.assertEqual(rendered, "w0 w1 w2 w3 ")

    def test_events_dropped_by_bounded_retention_leave_no_hole(self):
        broker = TurnEventBroker(max_events=4, max_bytes=10**6)
        for index in range(12):
            broker.publish("turn", "assistant.delta", {"text": f"w{index} "})
        expected = "".join(f"w{index} " for index in range(12))
        # The client's cursor points into a region that has already been evicted.
        self.assertEqual(self._client_text(broker, "turn", last_event_id=2), expected)

    def test_a_fresh_subscriber_with_no_cursor_sees_the_reply_once(self):
        broker = TurnEventBroker()
        for index in range(4):
            broker.publish("turn", "assistant.delta", {"text": f"w{index} "})
        self.assertEqual(self._client_text(broker, "turn", last_event_id=None), "w0 w1 w2 w3 ")
