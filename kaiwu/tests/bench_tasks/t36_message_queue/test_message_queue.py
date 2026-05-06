"""Tests for message queue system."""

import pytest
from topic import Topic, Message
from broker import Broker, DeadLetterQueue


class TestTopic:
    def test_publish_and_poll(self):
        t = Topic("events")
        t.subscribe("grp-a")
        msg = Message("m1", "events", {"data": 1})
        t.publish(msg)
        msgs = t.poll("grp-a", max_messages=1)
        assert len(msgs) == 1
        assert msgs[0].id == "m1"

    def test_poll_advances_offset(self):
        t = Topic("events")
        t.subscribe("grp-a")
        for i in range(5):
            t.publish(Message(f"m{i}", "events", i))
        t.poll("grp-a", max_messages=3)
        remaining = t.pending_count("grp-a")
        assert remaining == 2

    def test_poll_returns_only_available(self):
        """Polling more than available should return only what exists."""
        t = Topic("events")
        t.subscribe("grp-a")
        t.publish(Message("m1", "events", 1))
        t.publish(Message("m2", "events", 2))
        msgs = t.poll("grp-a", max_messages=10)
        assert len(msgs) == 2

    def test_multiple_groups_independent(self):
        t = Topic("events")
        t.subscribe("grp-a")
        t.subscribe("grp-b")
        t.publish(Message("m1", "events", 1))
        t.poll("grp-a", max_messages=1)
        # grp-b should still see the message
        msgs = t.poll("grp-b", max_messages=1)
        assert len(msgs) == 1

    def test_subscribe_starts_at_current_end(self):
        """New subscriber should not see messages published before subscription."""
        t = Topic("events")
        t.publish(Message("m1", "events", 1))
        t.subscribe("grp-late")
        t.publish(Message("m2", "events", 2))
        msgs = t.poll("grp-late", max_messages=10)
        assert len(msgs) == 1
        assert msgs[0].id == "m2"

    def test_ack_marks_message(self):
        t = Topic("events")
        t.subscribe("grp-a")
        t.publish(Message("m1", "events", 1))
        t.poll("grp-a", max_messages=1)
        assert t.ack("grp-a", "m1") is True
        # Find the message and check it's acked
        assert t._messages[0].acked is True

    def test_overflow_raises(self):
        t = Topic("events", max_size=2)
        t.publish(Message("m1", "events", 1))
        t.publish(Message("m2", "events", 2))
        with pytest.raises(OverflowError):
            t.publish(Message("m3", "events", 3))

    def test_unsubscribed_group_raises(self):
        t = Topic("events")
        with pytest.raises(KeyError):
            t.poll("unknown-group")


class TestBroker:
    def test_create_and_publish(self):
        b = Broker()
        b.create_topic("orders")
        msg = b.publish("orders", {"item": "book"})
        assert msg.id is not None

    def test_publish_to_missing_topic_raises(self):
        b = Broker()
        with pytest.raises(KeyError):
            b.publish("missing", {})

    def test_subscribe_and_poll(self):
        b = Broker()
        b.create_topic("orders")
        b.subscribe("orders", "processor")
        b.publish("orders", {"item": "book"})
        msgs = b.poll("orders", "processor")
        assert len(msgs) == 1

    def test_process_with_retry_success(self):
        b = Broker()
        b.create_topic("orders")
        b.subscribe("orders", "processor")
        b.publish("orders", {"item": "book"})
        results = []
        stats = b.process_with_retry("orders", "processor",
                                     lambda msg: results.append(msg.payload))
        assert stats["processed"] == 1
        assert stats["dead_lettered"] == 0
        assert results[0] == {"item": "book"}

    def test_process_with_retry_dead_letter(self):
        b = Broker()
        b.create_topic("orders")
        b.subscribe("orders", "processor")
        b.publish("orders", {"item": "book"})
        stats = b.process_with_retry("orders", "processor",
                                     lambda msg: (_ for _ in ()).throw(RuntimeError("fail")),
                                     max_retries=2)
        assert stats["dead_lettered"] == 1
        assert stats["retried"] == 2
        assert b.dlq.size() == 1

    def test_process_with_retry_retries_before_dlq(self):
        b = Broker()
        b.create_topic("orders")
        b.subscribe("orders", "processor")
        b.publish("orders", {"item": "book"})
        attempt_count = [0]

        def flaky(msg):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise RuntimeError("transient")

        stats = b.process_with_retry("orders", "processor", flaky, max_retries=3)
        assert stats["processed"] == 1
        assert stats["retried"] == 2

    def test_dlq_drain(self):
        b = Broker()
        b.create_topic("orders")
        b.subscribe("orders", "processor")
        b.publish("orders", {"item": "book"})
        b.process_with_retry("orders", "processor",
                             lambda msg: (_ for _ in ()).throw(RuntimeError("fail")),
                             max_retries=0)
        items = b.dlq.drain()
        assert len(items) == 1
        assert b.dlq.size() == 0
