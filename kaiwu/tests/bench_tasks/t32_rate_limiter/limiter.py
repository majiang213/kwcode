"""Rate limiter algorithms: token bucket and sliding window."""

import time
from collections import deque


class TokenBucket:
    """Token bucket rate limiter.

    Tokens are added at a fixed rate up to a maximum capacity.
    Each request consumes one token.
    """

    def __init__(self, capacity: int, refill_rate: float):
        """
        capacity: maximum number of tokens
        refill_rate: tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()

    def _refill(self, now: float) -> None:
        """Add tokens based on elapsed time since last refill."""
        elapsed = now - self._last_refill
        # Bug: multiplies elapsed by capacity instead of refill_rate
        new_tokens = elapsed * self.capacity
        self._tokens = min(self.capacity, self._tokens + new_tokens)
        self._last_refill = now

    def allow(self, now: float = None) -> bool:
        """Return True if the request is allowed (consumes one token)."""
        if now is None:
            now = time.monotonic()
        self._refill(now)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    def available_tokens(self, now: float = None) -> float:
        """Return current token count without consuming."""
        if now is None:
            now = time.monotonic()
        self._refill(now)
        return self._tokens


class SlidingWindowCounter:
    """Sliding window rate limiter using a fixed-size time window."""

    def __init__(self, limit: int, window_seconds: float):
        """
        limit: max requests allowed in the window
        window_seconds: size of the sliding window
        """
        self.limit = limit
        self.window_seconds = window_seconds
        self._timestamps: deque = deque()

    def allow(self, now: float = None) -> bool:
        """Return True if the request is within the rate limit."""
        if now is None:
            now = time.monotonic()
        self._evict_old(now)
        if len(self._timestamps) < self.limit:
            self._timestamps.append(now)
            return True
        return False

    def _evict_old(self, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def current_count(self, now: float = None) -> int:
        """Return number of requests in the current window."""
        if now is None:
            now = time.monotonic()
        self._evict_old(now)
        return len(self._timestamps)
