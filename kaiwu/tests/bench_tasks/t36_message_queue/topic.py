"""Message queue: broker, consumer groups, and dead-letter queue."""

from typing import Any, Callable, Optional
from collections import deque
import time


class Message:
    def __init__(self, msg_id: str, topic: str, payload: Any,
                 timestamp: float = None):
        self.id = msg_id
        self.topic = topic
        self.payload = payload
        self.timestamp = timestamp or time.monotonic()
        self.delivery_count = 0
        self.acked = False


class Topic:
    """A named message topic with ordered message storage."""

    def __init__(self, name: str, max_size: int = 10000):
        self.name = name
        self.max_size = max_size
        self._messages: list[Message] = []
        self._offsets: dict[str, int] = {}  # consumer_group -> next offset

    def publish(self, message: Message) -> None:
        if len(self._messages) >= self.max_size:
            raise OverflowError(f"Topic '{self.name}' is full")
        self._messages.append(message)

    def subscribe(self, group: str) -> None:
        """Register a consumer group starting from the current end."""
        if group not in self._offsets:
            self._offsets[group] = len(self._messages)

    def poll(self, group: str, max_messages: int = 1) -> list[Message]:
        """Return up to max_messages for the consumer group."""
        if group not in self._offsets:
            raise KeyError(f"Consumer group '{group}' not subscribed to '{self.name}'")
        offset = self._offsets[group]
        batch = self._messages[offset: offset + max_messages]
        for msg in batch:
            msg.delivery_count += 1
        # Bug: advances offset by max_messages instead of len(batch)
        self._offsets[group] = offset + max_messages
        return batch

    def ack(self, group: str, msg_id: str) -> bool:
        """Acknowledge a message (mark as processed)."""
        for msg in self._messages:
            if msg.id == msg_id:
                msg.acked = True
                return True
        return False

    def pending_count(self, group: str) -> int:
        """Number of undelivered messages for a consumer group."""
        offset = self._offsets.get(group, 0)
        return len(self._messages) - offset

    def size(self) -> int:
        return len(self._messages)
