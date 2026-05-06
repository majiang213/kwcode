"""Cache invalidation strategies: tag-based and dependency-based."""

from cache import LRUCache
from typing import Any, Optional
import time


class TaggedCache:
    """Cache that supports tag-based invalidation.

    Each entry can be associated with one or more tags.
    Invalidating a tag removes all entries with that tag.
    """

    def __init__(self, max_size: int = 1000, default_ttl: float = 0):
        self._cache = LRUCache(max_size=max_size, default_ttl=default_ttl)
        # tag -> set of keys
        self._tag_index: dict[str, set[str]] = {}
        # key -> set of tags
        self._key_tags: dict[str, set[str]] = {}

    def get(self, key: str, now: float = None) -> Optional[Any]:
        return self._cache.get(key, now=now)

    def set(self, key: str, value: Any, tags: list[str] = None,
            ttl: float = None, now: float = None) -> None:
        self._cache.set(key, value, ttl=ttl, now=now)
        tags = tags or []
        self._key_tags[key] = set(tags)
        for tag in tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(key)

    def invalidate_tag(self, tag: str) -> int:
        """Remove all entries associated with the given tag.

        Returns the number of entries removed.
        """
        keys = self._tag_index.pop(tag, set())
        count = 0
        for key in keys:
            if self._cache.delete(key):
                count += 1
            # Bug: does not clean up _key_tags for the removed key
        return count

    def invalidate_key(self, key: str) -> bool:
        """Remove a single entry and clean up its tag associations."""
        if not self._cache.delete(key):
            return False
        for tag in self._key_tags.pop(key, set()):
            if tag in self._tag_index:
                self._tag_index[tag].discard(key)
        return True

    def tags_for_key(self, key: str) -> set[str]:
        return set(self._key_tags.get(key, set()))

    def keys_for_tag(self, tag: str) -> set[str]:
        return set(self._tag_index.get(tag, set()))

    @property
    def stats(self) -> dict:
        return {
            "hits": self._cache.hits,
            "misses": self._cache.misses,
            "evictions": self._cache.evictions,
            "size": self._cache.size(),
        }
