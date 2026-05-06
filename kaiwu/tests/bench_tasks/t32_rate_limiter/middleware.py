"""Rate limiter middleware and per-key limiter registry."""

from limiter import TokenBucket, SlidingWindowCounter
from typing import Callable, Any


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded."""
    pass


class PerKeyLimiter:
    """Manages separate rate limiters per key (e.g., per IP or user)."""

    def __init__(self, factory: Callable[[], Any]):
        """factory: callable that creates a new limiter instance."""
        self._factory = factory
        self._limiters: dict[str, Any] = {}

    def get_or_create(self, key: str) -> Any:
        """Get existing limiter for key, or create a new one."""
        if key not in self._limiters:
            self._limiters[key] = self._factory()
        return self._limiters[key]

    def allow(self, key: str, now: float = None) -> bool:
        """Check if request from key is allowed."""
        limiter = self.get_or_create(key)
        return limiter.allow(now)

    def reset(self, key: str) -> None:
        """Remove the limiter for a key (resets its state)."""
        self._limiters.pop(key, None)

    def active_keys(self) -> list[str]:
        return list(self._limiters.keys())


class RateLimitMiddleware:
    """Middleware that enforces rate limits before calling a handler."""

    def __init__(self, limiter: PerKeyLimiter, key_fn: Callable[[Any], str]):
        """
        limiter: the PerKeyLimiter to use
        key_fn: extracts the rate-limit key from a request
        """
        self._limiter = limiter
        self._key_fn = key_fn
        self.rejected_count = 0
        self.allowed_count = 0

    def __call__(self, request: Any, handler: Callable) -> Any:
        """Process request through rate limiter then handler."""
        key = self._key_fn(request)
        if not self._limiter.allow(key):
            self.rejected_count += 1
            raise RateLimitExceeded(f"Rate limit exceeded for key: {key}")
        self.allowed_count += 1
        return handler(request)
