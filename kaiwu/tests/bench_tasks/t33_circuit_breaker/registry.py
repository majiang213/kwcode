"""Circuit breaker registry and health monitoring."""

from breaker import CircuitBreaker, State, CircuitBreakerError
from typing import Callable, Any, Optional
import time


class ServiceHealth:
    """Tracks health metrics for a service."""

    def __init__(self, name: str):
        self.name = name
        self.total_calls = 0
        self.total_failures = 0
        self.total_rejections = 0

    @property
    def failure_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_failures / self.total_calls

    def record_call(self, success: bool) -> None:
        self.total_calls += 1
        if not success:
            self.total_failures += 1

    def record_rejection(self) -> None:
        self.total_rejections += 1


class CircuitBreakerRegistry:
    """Central registry for multiple circuit breakers."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._health: dict[str, ServiceHealth] = {}

    def register(self, name: str, breaker: CircuitBreaker) -> None:
        self._breakers[name] = breaker
        self._health[name] = ServiceHealth(name)

    def get(self, name: str) -> Optional[CircuitBreaker]:
        return self._breakers.get(name)

    def call(self, name: str, fn: Callable, now: float = None) -> Any:
        """Call fn through the named circuit breaker, tracking health."""
        breaker = self._breakers.get(name)
        if breaker is None:
            raise KeyError(f"No circuit breaker registered for '{name}'")

        health = self._health[name]
        try:
            result = breaker.call(fn, now=now)
            health.record_call(success=True)
            return result
        except CircuitBreakerError:
            health.record_rejection()
            raise
        except Exception:
            health.record_call(success=False)
            raise

    def health_report(self) -> dict[str, dict]:
        """Return health summary for all registered services."""
        report = {}
        for name, health in self._health.items():
            breaker = self._breakers[name]
            report[name] = {
                "state": breaker.state.value,
                "total_calls": health.total_calls,
                "total_failures": health.total_failures,
                "total_rejections": health.total_rejections,
                "failure_rate": health.failure_rate,
            }
        return report

    def reset(self, name: str) -> None:
        """Force a circuit breaker back to CLOSED state."""
        breaker = self._breakers.get(name)
        if breaker:
            breaker._transition(State.CLOSED, time.monotonic())
