"""Frontend auth service that consumes the backend auth API."""

import time
from typing import Optional
from backend import create_token, verify_token, refresh_token, get_current_user


class AuthClient:
    """Frontend auth client that manages tokens and session state."""

    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._user_id: Optional[str] = None
        self._role: Optional[str] = None

    def login(self, user_id: str, role: str, now: float = None) -> bool:
        """Authenticate and store token. Returns True on success."""
        if now is None:
            now = time.time()
        response = create_token(user_id, role, now=now)
        self._token = response["token"]
        self._user_id = response["user_id"]
        self._role = response["role"]
        # Bug: expects 'expiresAt' (unix timestamp) but backend returns 'expires_in' (seconds)
        # Should be: self._expires_at = response["expiresAt"]
        self._expires_at = response.get("expiresAt")
        return self._token is not None

    def is_authenticated(self, now: float = None) -> bool:
        """Return True if we have a valid, non-expired token."""
        if self._token is None:
            return False
        if self._expires_at is None:
            return False
        if now is None:
            now = time.time()
        return now < self._expires_at

    def get_auth_header(self) -> Optional[str]:
        """Return the Authorization header value for API requests."""
        if self._token is None:
            return None
        return f"Bearer {self._token}"

    def refresh(self, now: float = None) -> bool:
        """Refresh the current token. Returns True on success."""
        if self._token is None:
            return False
        if now is None:
            now = time.time()
        # Bug: sends 'Token <token>' instead of 'Bearer <token>'
        auth_header = f"Token {self._token}"
        # Simulate calling backend refresh endpoint
        new_response = refresh_token(self._token, now=now)
        if new_response is None:
            return False
        self._token = new_response["token"]
        self._expires_at = new_response.get("expiresAt")
        return True

    def get_user_info(self, now: float = None) -> Optional[dict]:
        """Get current user info from backend."""
        header = self.get_auth_header()
        if header is None:
            return None
        return get_current_user(header, now=now)

    def logout(self) -> None:
        self._token = None
        self._expires_at = None
        self._user_id = None
        self._role = None
