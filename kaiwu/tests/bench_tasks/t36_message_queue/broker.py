"""Message broker and dead-letter queue."""

from topic import Topic, Message
from typing import Any, Callable, Optional
import uuid
import time


class DeadLetterQueue:
    """Stores messages that failed processing after max retries."""

    def __init__(self):
        self._messages: list[tuple[Message, str]] = []  # (msg, reason)

    def add(self, message: Message, reason: str) -> None:
        self._messages.append((message, reason))

    def drain(self) -> list[tuple[Message, str]]:
        items = list(self._messages)
        self._messages.clear()
        return items

    def size(self) -> int:
        return len(self._messages)


class Broker:
    """Central message broker managing topics and routing."""

    def __init__(self):
        self._topics: dict[str, Topic] = {}
        self._dlq = DeadLetterQueue()
        self._msg_counter = 0

    def create_topic(self, name: str, max_size: int = 10000) -> Topic:
        if name in self._topics:
            raise ValueError(f"Topic '{name}' already exists")
        topic = Topic(name, max_size)
        self._topics[name] = topic
        return topic

    def get_topic(self, name: str) -> Optional[Topic]:
        return self._topics.get(name)

    def publish(self, topic_name: str, payload: Any,
                msg_id: str = None) -> Message:
        topic = self._topics.get(topic_name)
        if topic is None:
            raise KeyError(f"Topic '{topic_name}' does not exist")
        self._msg_counter += 1
        msg = Message(
            msg_id=msg_id or f"msg-{self._msg_counter}",
            topic=topic_name,
            payload=payload,
        )
        topic.publish(msg)
        return msg

    def subscribe(self, topic_name: str, group: str) -> None:
        topic = self._topics.get(topic_name)
        if topic is None:
            raise KeyError(f"Topic '{topic_name}' does not exist")
        topic.subscribe(group)

    def poll(self, topic_name: str, group: str,
             max_messages: int = 1) -> list[Message]:
        topic = self._topics.get(topic_name)
        if topic is None:
            raise KeyError(f"Topic '{topic_name}' does not exist")
        return topic.poll(group, max_messages)

    def process_with_retry(self, topic_name: str, group: str,
                           handler: Callable[[Message], None],
                           max_retries: int = 3) -> dict:
        """Poll one message and process it, retrying on failure.

        Returns dict with 'processed', 'retried', 'dead_lettered' counts.
        """
        messages = self.poll(topic_name, group, max_messages=1)
        if not messages:
            return {"processed": 0, "retried": 0, "dead_lettered": 0}

        msg = messages[0]
        stats = {"processed": 0, "retried": 0, "dead_lettered": 0}

        for attempt in range(max_retries + 1):
            try:
                handler(msg)
                topic = self._topics[topic_name]
                topic.ack(group, msg.id)
                stats["processed"] = 1
                return stats
            except Exception as e:
                if attempt < max_retries:
                    stats["retried"] += 1
                else:
                    self._dlq.add(msg, str(e))
                    stats["dead_lettered"] = 1

        return stats

    @property
    def dlq(self) -> DeadLetterQueue:
        return self._dlq

    def topic_names(self) -> list[str]:
        return list(self._topics.keys())
