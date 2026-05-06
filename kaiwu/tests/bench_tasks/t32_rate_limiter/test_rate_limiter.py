"""Tests for rate limiter system."""

import pytest
import time
from limiter import TokenBucket, SlidingWindowCounter
from middleware import PerKeyLimiter, RateLimitMiddleware, RateLimitExceeded


class TestTokenBucket:
    def test_initial_full_bucket(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.available_tokens(now=0.0) == 10.0

    def test_allows_up_to_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        results = [bucket.allow(now=0.0) for _ in range(5)]
        assert all(results)

    def test_rejects_when_empty(self):
        bucket = TokenBucket(capacity=3, refill_rate=1.0)
        for _ in range(3):
            bucket.allow(now=0.0)
        assert bucket.allow(now=0.0) is False

    def test_refills_over_time(self):
        """After 2 seconds at rate=1, should have 2 new tokens."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        # Drain 5 tokens
        for _ in range(5):
            bucket.allow(now=0.0)
        assert bucket.available_tokens(now=0.0) == 0.0
        # 2 seconds later, should have 2 tokens
        tokens = bucket.available_tokens(now=2.0)
        assert abs(tokens - 2.0) < 0.01

    def test_refill_does_not_exceed_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=10.0)
        # Drain all
        for _ in range(5):
            bucket.allow(now=0.0)
        # Wait a long time
        tokens = bucket.available_tokens(now=100.0)
        assert tokens == 5.0

    def test_refill_rate_respected(self):
        """Rate=2 tokens/sec: after 1 second, 2 tokens added."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0)
        for _ in range(10):
            bucket.allow(now=0.0)
        tokens = bucket.available_tokens(now=1.0)
        assert abs(tokens - 2.0) < 0.01


class TestSlidingWindow:
    def test_allows_within_limit(self):
        sw = SlidingWindowCounter(limit=5, window_seconds=1.0)
        results = [sw.allow(now=float(i) * 0.1) for i in range(5)]
        assert all(results)

    def test_rejects_over_limit(self):
        sw = SlidingWindowCounter(limit=3, window_seconds=1.0)
        for i in range(3):
            sw.allow(now=0.0)
        assert sw.allow(now=0.0) is False

    def test_old_requests_evicted(self):
        """Requests older than window_seconds should not count."""
        sw = SlidingWindowCounter(limit=3, window_seconds=1.0)
        for _ in range(3):
            sw.allow(now=0.0)
        # 1.1 seconds later, old requests are outside window
        assert sw.allow(now=1.1) is True

    def test_current_count(self):
        sw = SlidingWindowCounter(limit=10, window_seconds=2.0)
        sw.allow(now=0.0)
        sw.allow(now=0.5)
        sw.allow(now=1.0)
        assert sw.current_count(now=1.0) == 3
        # At t=2.1, first request (t=0.0) is outside window
        assert sw.current_count(now=2.1) == 2

    def test_boundary_exactly_at_window_edge(self):
        """Request at exactly window_seconds ago should be evicted."""
        sw = SlidingWindowCounter(limit=2, window_seconds=1.0)
        sw.allow(now=0.0)
        sw.allow(now=0.5)
        # At t=1.0, the request at t=0.0 is exactly at the boundary (evicted)
        assert sw.current_count(now=1.0) == 1


class TestPerKeyLimiter:
    def test_separate_limits_per_key(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=2, refill_rate=1.0))
        assert limiter.allow("user-1", now=0.0) is True
        assert limiter.allow("user-1", now=0.0) is True
        assert limiter.allow("user-1", now=0.0) is False
        # user-2 has its own bucket
        assert limiter.allow("user-2", now=0.0) is True

    def test_reset_clears_limiter(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=1, refill_rate=1.0))
        limiter.allow("user-1", now=0.0)
        assert limiter.allow("user-1", now=0.0) is False
        limiter.reset("user-1")
        assert limiter.allow("user-1", now=0.0) is True


class TestRateLimitMiddleware:
    def test_allows_request(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=5, refill_rate=1.0))
        mw = RateLimitMiddleware(limiter, key_fn=lambda req: req["ip"])
        result = mw({"ip": "1.2.3.4"}, lambda req: "ok")
        assert result == "ok"
        assert mw.allowed_count == 1

    def test_rejects_over_limit(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=1, refill_rate=0.0))
        mw = RateLimitMiddleware(limiter, key_fn=lambda req: req["ip"])
        mw({"ip": "1.2.3.4"}, lambda req: "ok")
        with pytest.raises(RateLimitExceeded):
            mw({"ip": "1.2.3.4"}, lambda req: "ok")
        assert mw.rejected_count == 1

    def test_different_keys_independent(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=1, refill_rate=0.0))
        mw = RateLimitMiddleware(limiter, key_fn=lambda req: req["ip"])
        mw({"ip": "1.1.1.1"}, lambda req: "ok")
        # Different IP should still be allowed
        result = mw({"ip": "2.2.2.2"}, lambda req: "ok")
        assert result == "ok"
