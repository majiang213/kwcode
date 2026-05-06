"""Write-through and write-behind cache policies."""

from cache import LRUCache
from typing import Any, Callable, Optional
import time


class WriteThroughCache:
    """Cache that writes to both cache and backing store on set().

    On get(), returns from cache if present; otherwise loads from store,
    populates cache, and returns the value.
    """

    def __init__(self, cache: LRUCache,
                 loader: Callable[[str], Any],
                 writer: Callable[[str, Any], None]):
        self._cache = cache
        self._loader = loader
        self._writer = writer

    def get(self, key: str, now: float = None) -> Optional[Any]:
        value = self._cache.get(key, now=now)
        if value is not None:
            return value
        # Cache miss: load from backing store
        value = self._loader(key)
        if value is not None:
            self._cache.set(key, value, now=now)
        return value

    def set(self, key: str, value: Any, now: float = None) -> None:
        """Write to both cache and backing store."""
        # Bug: writes to cache first, then backing store — but if writer
        # raises, cache has stale data. The real bug: writer is never called.
        self._cache.set(key, value, now=now)
        # Missing: self._writer(key, value)

    def delete(self, key: str) -> None:
        self._cache.delete(key)
        self._writer(key, None)
