"""Tests for frontend-backend auth interface consistency."""

import pytest
import time
from backend import create_token, verify_token, get_current_user, refresh_token
from frontend import AuthClient


class TestBackendTokenCreation:
    def test_token_is_string(self):
        resp = create_token("user-1", "admin", now=1000.0)
        assert isinstance(resp["token"], str)
        assert len(resp["token"].split(".")) == 3

    def test_token_contains_user_id(self):
        resp = create_token("user-42", "viewer", now=1000.0)
        claims = verify_token(resp["token"], now=1000.0)
        assert claims["user_id"] == "user-42"

    def test_response_has_expires_at(self):
        """Backend must return 'expiresAt' as a unix timestamp, not 'expires_in'."""
        resp = create_token("user-1", "admin", now=1000.0)
        assert "expiresAt" in resp, "Backend must return 'expiresAt' (unix timestamp)"
        assert resp["expiresAt"] == pytest.approx(1000.0 + 3600, abs=1)

    def test_token_expires(self):
        resp = create_token("user-1", "admin", now=1000.0)
        claims = verify_token(resp["token"], now=5000.0)
        assert claims is None

    def test_invalid_token_rejected(self):
        assert verify_token("not.a.token") is None
        assert verify_token("a.b.c") is None


class TestBackendGetCurrentUser:
    def test_returns_user_from_valid_token(self):
        resp = create_token("user-99", "editor", now=1000.0)
        user = get_current_user(f"Bearer {resp['token']}", now=1000.0)
        assert user is not None
        assert user["user_id"] == "user-99"
        assert user["role"] == "editor"

    def test_rejects_missing_bearer_prefix(self):
        resp = create_token("user-1", "admin", now=1000.0)
        user = get_current_user(resp["token"], now=1000.0)
        assert user is None

    def test_rejects_wrong_prefix(self):
        resp = create_token("user-1", "admin", now=1000.0)
        user = get_current_user(f"Token {resp['token']}", now=1000.0)
        assert user is None

    def test_rejects_expired_token(self):
        resp = create_token("user-1", "admin", now=1000.0)
        user = get_current_user(f"Bearer {resp['token']}", now=5000.0)
        assert user is None


class TestFrontendAuthClient:
    def test_login_sets_authenticated(self):
        client = AuthClient()
        ok = client.login("user-1", "admin", now=1000.0)
        assert ok is True
        assert client.is_authenticated(now=1000.0) is True

    def test_is_authenticated_false_before_login(self):
        client = AuthClient()
        assert client.is_authenticated(now=1000.0) is False

    def test_is_authenticated_false_after_expiry(self):
        client = AuthClient()
        client.login("user-1", "admin", now=1000.0)
        assert client.is_authenticated(now=5000.0) is False

    def test_get_auth_header_format(self):
        client = AuthClient()
        client.login("user-1", "admin", now=1000.0)
        header = client.get_auth_header()
        assert header is not None
        assert header.startswith("Bearer ")

    def test_get_user_info_returns_correct_user(self):
        client = AuthClient()
        client.login("user-42", "viewer", now=1000.0)
        user = client.get_user_info(now=1000.0)
        assert user is not None
        assert user["user_id"] == "user-42"

    def test_refresh_extends_session(self):
        client = AuthClient()
        client.login("user-1", "admin", now=1000.0)
        ok = client.refresh(now=1000.0)
        assert ok is True
        # After refresh, should still be authenticated
        assert client.is_authenticated(now=1000.0) is True

    def test_logout_clears_state(self):
        client = AuthClient()
        client.login("user-1", "admin", now=1000.0)
        client.logout()
        assert client.is_authenticated(now=1000.0) is False
        assert client.get_auth_header() is None
