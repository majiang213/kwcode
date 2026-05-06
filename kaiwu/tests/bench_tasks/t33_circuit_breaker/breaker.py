"""Circuit breaker state machine."""

import time
from enum import Enum


class State(Enum):
    CLOSED = "closed"       # normal operation
    OPEN = "open"           # failing, reject all calls
    HALF_OPEN = "half_open" # testing if service recovered


class CircuitBreakerError(Exception):
    """Raised when circuit is open and call is rejected."""
    pass


class CircuitBreaker:
    """Circuit breaker that wraps calls to a potentially failing service.

    States:
      CLOSED  -> failures accumulate; if >= failure_threshold, -> OPEN
      OPEN    -> all calls rejected; after recovery_timeout, -> HALF_OPEN
      HALF_OPEN -> one probe call allowed; success -> CLOSED, failure -> OPEN
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 5.0,
                 success_threshold: int = 1):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = State.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float = None
        self.call_count = 0
        self.rejected_count = 0

    @property
    def state(self) -> State:
        return self._state

    def _transition(self, new_state: State, now: float) -> None:
        self._state = new_state
        if new_state == State.OPEN:
            self._opened_at = now
            self._failure_count = 0
            self._success_count = 0
        elif new_state == State.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
        elif new_state == State.HALF_OPEN:
            self._success_count = 0

    def _check_recovery(self, now: float) -> None:
        """Transition OPEN -> HALF_OPEN if recovery timeout has elapsed."""
        if self._state == State.OPEN and self._opened_at is not None:
            # Bug: uses < instead of >= so it never transitions to HALF_OPEN
            if now - self._opened_at < self.recovery_timeout:
                self._transition(State.HALF_OPEN, now)

    def call(self, fn, now: float = None):
        """Execute fn through the circuit breaker.

        Raises CircuitBreakerError if circuit is open.
        """
        if now is None:
            now = time.monotonic()

        self._check_recovery(now)

        if self._state == State.OPEN:
            self.rejected_count += 1
            raise CircuitBreakerError("Circuit is open")

        self.call_count += 1
        try:
            result = fn()
            self._on_success(now)
            return result
        except Exception as e:
            self._on_failure(now)
            raise

    def _on_success(self, now: float) -> None:
        if self._state == State.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._transition(State.CLOSED, now)
        elif self._state == State.CLOSED:
            self._failure_count = 0

    def _on_failure(self, now: float) -> None:
        self._failure_count += 1
        if self._state == State.HALF_OPEN:
            self._transition(State.OPEN, now)
        elif self._state == State.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._transition(State.OPEN, now)
