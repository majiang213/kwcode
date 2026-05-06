"""Tests for cache invalidation system."""

import pytest
import time
from cache import LRUCache, CacheEntry
from invalidation import TaggedCache
from policy import WriteThroughCache


class TestLRUCache:
    def test_basic_set_get(self):
        c = LRUCache(max_size=10)
        c.set("k", "v", now=0.0)
        assert c.get("k", now=0.0) == "v"

    def test_miss_returns_none(self):
        c = LRUCache(max_size=10)
        assert c.get("missing", now=0.0) is None

    def test_lru_eviction(self):
        c = LRUCache(max_size=3)
        c.set("a", 1, now=0.0)
        c.set("b", 2, now=0.0)
        c.set("c", 3, now=0.0)
        # Access 'a' to make it recently used
        c.get("a", now=0.0)
        # Adding 'd' should evict 'b' (LRU)
        c.set("d", 4, now=0.0)
        assert c.get("b", now=0.0) is None
        assert c.get("a", now=0.0) == 1

    def test_ttl_expiry(self):
        c = LRUCache(max_size=10, default_ttl=1.0)
        c.set("k", "v", now=0.0)
        assert c.get("k", now=0.5) == "v"
        assert c.get("k", now=1.5) is None

    def test_expired_entry_removed_from_store(self):
        """After TTL expiry, the key should no longer occupy cache space."""
        c = LRUCache(max_size=2, default_ttl=1.0)
        c.set("a", 1, now=0.0)
        c.set("b", 2, now=0.0)
        # Expire 'a'
        c.get("a", now=2.0)
        # Now we should be able to add 'c' without evicting 'b'
        c.set("c", 3, now=2.0)
        assert c.get("b", now=2.0) == 2
        assert c.get("c", now=2.0) == 3

    def test_hit_miss_counters(self):
        c = LRUCache(max_size=10)
        c.set("k", "v", now=0.0)
        c.get("k", now=0.0)
        c.get("missing", now=0.0)
        assert c.hits == 1
        assert c.misses == 1

    def test_delete(self):
        c = LRUCache(max_size=10)
        c.set("k", "v", now=0.0)
        assert c.delete("k") is True
        assert c.get("k", now=0.0) is None
        assert c.delete("k") is False


class TestTaggedCache:
    def test_set_and_get(self):
        tc = TaggedCache()
        tc.set("user:1", {"name": "Alice"}, tags=["user", "user:1"])
        assert tc.get("user:1") == {"name": "Alice"}

    def test_invalidate_tag_removes_entries(self):
        tc = TaggedCache()
        tc.set("user:1", "Alice", tags=["user"])
        tc.set("user:2", "Bob", tags=["user"])
        tc.set("post:1", "Hello", tags=["post"])
        count = tc.invalidate_tag("user")
        assert count == 2
        assert tc.get("user:1") is None
        assert tc.get("user:2") is None
        assert tc.get("post:1") == "Hello"

    def test_invalidate_tag_cleans_key_tags(self):
        """After tag invalidation, the key's tag associations should be cleaned."""
        tc = TaggedCache()
        tc.set("user:1", "Alice", tags=["user", "active"])
        tc.invalidate_tag("user")
        # user:1 is gone; its tag associations should be cleaned up
        # Re-adding user:1 should not have stale tag associations
        tc.set("user:1", "Alice2", tags=["user"])
        assert tc.tags_for_key("user:1") == {"user"}

    def test_invalidate_key(self):
        tc = TaggedCache()
        tc.set("user:1", "Alice", tags=["user"])
        assert tc.invalidate_key("user:1") is True
        assert tc.get("user:1") is None
        assert "user:1" not in tc.keys_for_tag("user")

    def test_keys_for_tag(self):
        tc = TaggedCache()
        tc.set("a", 1, tags=["group"])
        tc.set("b", 2, tags=["group"])
        tc.set("c", 3, tags=["other"])
        assert tc.keys_for_tag("group") == {"a", "b"}


class TestWriteThroughCache:
    def test_get_populates_cache_on_miss(self):
        backing = {"k": "v"}
        cache = LRUCache(max_size=10)
        wt = WriteThroughCache(
            cache,
            loader=lambda k: backing.get(k),
            writer=lambda k, v: backing.update({k: v}) if v is not None else backing.pop(k, None),
        )
        result = wt.get("k", now=0.0)
        assert result == "v"
        # Should now be in cache
        assert cache.get("k", now=0.0) == "v"

    def test_set_writes_to_backing_store(self):
        backing = {}
        cache = LRUCache(max_size=10)
        wt = WriteThroughCache(
            cache,
            loader=lambda k: backing.get(k),
            writer=lambda k, v: backing.update({k: v}) if v is not None else None,
        )
        wt.set("k", "new_value", now=0.0)
        assert backing.get("k") == "new_value"

    def test_set_updates_cache(self):
        backing = {}
        cache = LRUCache(max_size=10)
        wt = WriteThroughCache(
            cache,
            loader=lambda k: backing.get(k),
            writer=lambda k, v: backing.update({k: v}) if v is not None else None,
        )
        wt.set("k", "value", now=0.0)
        assert cache.get("k", now=0.0) == "value"
