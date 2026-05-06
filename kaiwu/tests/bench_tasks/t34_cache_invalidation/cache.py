"""Cache store with TTL and LRU eviction."""

import time
from collections import OrderedDict
from typing import Any, Optional


class CacheEntry:
    def __init__(self, value: Any, ttl: float, created_at: float):
        self.value = value
        self.ttl = ttl
        self.created_at = created_at

    def is_expired(self, now: float) -> bool:
        if self.ttl <= 0:
            return False  # no expiry
        return now - self.created_at >= self.ttl


class LRUCache:
    """LRU cache with optional per-entry TTL."""

    def __init__(self, max_size: int, default_ttl: float = 0):
        self.max_size = max_size
        self.default_ttl = default_ttl
        # OrderedDict: most-recently-used at end
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str, now: float = None) -> Optional[Any]:
        if now is None:
            now = time.monotonic()
        if key not in self._store:
            self.misses += 1
            return None
        entry = self._store[key]
        if entry.is_expired(now):
            # Bug: does not remove the expired entry from the store
            self.misses += 1
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: float = None, now: float = None) -> None:
        if now is None:
            now = time.monotonic()
        effective_ttl = ttl if ttl is not None else self.default_ttl
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = CacheEntry(value, effective_ttl, now)
        if len(self._store) > self.max_size:
            self._evict_lru()

    def _evict_lru(self) -> None:
        """Remove the least-recently-used entry."""
        self._store.popitem(last=False)
        self.evictions += 1

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)

    def keys(self) -> list[str]:
        return list(self._store.keys())
