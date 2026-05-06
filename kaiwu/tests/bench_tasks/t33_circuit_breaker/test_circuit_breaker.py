"""Tests for circuit breaker system."""

import pytest
from breaker import CircuitBreaker, State, CircuitBreakerError
from registry import CircuitBreakerRegistry


def ok():
    return "ok"


def fail():
    raise RuntimeError("service error")


class TestCircuitBreakerClosed:
    def test_allows_calls_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        result = cb.call(ok, now=0.0)
        assert result == "ok"

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            try:
                cb.call(fail, now=0.0)
            except RuntimeError:
                pass
        assert cb.state == State.CLOSED

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            try:
                cb.call(fail, now=0.0)
            except RuntimeError:
                pass
        assert cb.state == State.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        cb.call(ok, now=0.0)
        # After success, failure count resets; need 3 more failures to open
        for _ in range(2):
            try:
                cb.call(fail, now=0.0)
            except RuntimeError:
                pass
        assert cb.state == State.CLOSED


class TestCircuitBreakerOpen:
    def test_rejects_calls_when_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        assert cb.state == State.OPEN
        with pytest.raises(CircuitBreakerError):
            cb.call(ok, now=0.0)

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        assert cb.state == State.OPEN
        # After recovery_timeout, should transition to HALF_OPEN
        cb.call(ok, now=6.0)
        # If it got here without CircuitBreakerError, it transitioned correctly
        assert cb.state == State.CLOSED

    def test_stays_open_before_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        with pytest.raises(CircuitBreakerError):
            cb.call(ok, now=3.0)
        assert cb.state == State.OPEN


class TestCircuitBreakerHalfOpen:
    def test_success_in_half_open_closes(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        # Trigger half-open
        result = cb.call(ok, now=6.0)
        assert result == "ok"
        assert cb.state == State.CLOSED

    def test_failure_in_half_open_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
        try:
            cb.call(fail, now=0.0)
        except RuntimeError:
            pass
        # Trigger half-open then fail
        try:
            cb.call(fail, now=6.0)
        except RuntimeError:
            pass
        assert cb.state == State.OPEN


class TestRegistry:
    def test_register_and_call(self):
        reg = CircuitBreakerRegistry()
        reg.register("svc-a", CircuitBreaker(failure_threshold=3))
        result = reg.call("svc-a", ok, now=0.0)
        assert result == "ok"

    def test_health_report(self):
        reg = CircuitBreakerRegistry()
        reg.register("svc-a", CircuitBreaker(failure_threshold=3))
        reg.call("svc-a", ok, now=0.0)
        try:
            reg.call("svc-a", fail, now=0.0)
        except RuntimeError:
            pass
        report = reg.health_report()
        assert report["svc-a"]["total_calls"] == 2
        assert report["svc-a"]["total_failures"] == 1
        assert abs(report["svc-a"]["failure_rate"] - 0.5) < 0.01

    def test_rejection_tracked(self):
        reg = CircuitBreakerRegistry()
        reg.register("svc-a", CircuitBreaker(failure_threshold=1))
        try:
            reg.call("svc-a", fail, now=0.0)
        except RuntimeError:
            pass
        try:
            reg.call("svc-a", ok, now=0.0)
        except CircuitBreakerError:
            pass
        report = reg.health_report()
        assert report["svc-a"]["total_rejections"] == 1

    def test_unknown_service_raises(self):
        reg = CircuitBreakerRegistry()
        with pytest.raises(KeyError):
            reg.call("unknown", ok)
