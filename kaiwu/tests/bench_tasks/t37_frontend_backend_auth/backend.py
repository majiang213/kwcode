"""
Frontend-backend auth interface mismatch task.

Backend: JWT auth API
Frontend client: auth service that calls the backend

Bugs:
1. backend.py: token expiry field is named 'expires_in' but frontend expects 'expiresAt' (timestamp)
2. frontend.py: refresh logic sends wrong header format ('Token' instead of 'Bearer')
3. backend.py: /me endpoint reads user from wrong token claim ('sub' vs 'user_id')
"""

import time
import hmac
import hashlib
import json
import base64
from typing import Optional


SECRET = "test-secret-key"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(header: str, payload: str) -> str:
    msg = f"{header}.{payload}".encode()
    sig = hmac.new(SECRET.encode(), msg, hashlib.sha256).digest()
    return _b64(sig)


def create_token(user_id: str, role: str, now: float = None) -> dict:
    """Create a JWT-like token. Returns the full auth response dict."""
    if now is None:
        now = time.time()
    exp = now + 3600

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_data = {"user_id": user_id, "role": role, "exp": exp, "iat": now}
    payload = _b64(json.dumps(payload_data).encode())
    sig = _sign(header, payload)
    token = f"{header}.{payload}.{sig}"

    # Bug: returns 'expires_in' (seconds) but frontend expects 'expiresAt' (unix timestamp)
    return {
        "token": token,
        "expires_in": 3600,
        "user_id": user_id,
        "role": role,
    }


def verify_token(token: str, now: float = None) -> Optional[dict]:
    """Verify token and return claims, or None if invalid/expired."""
    if now is None:
        now = time.time()
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        expected_sig = _sign(header, payload)
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = 4 - len(payload) % 4
        payload_data = json.loads(base64.urlsafe_b64decode(payload + "=" * padding))
        if payload_data.get("exp", 0) < now:
            return None
        return payload_data
    except Exception:
        return None


def get_current_user(auth_header: str, now: float = None) -> Optional[dict]:
    """Extract user info from Authorization header.

    Expects: 'Bearer <token>'
    Returns user dict with 'user_id' and 'role', or None.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    claims = verify_token(token, now=now)
    if claims is None:
        return None
    # Bug: reads 'sub' instead of 'user_id' from claims
    return {"user_id": claims.get("sub"), "role": claims.get("role")}


def refresh_token(old_token: str, now: float = None) -> Optional[dict]:
    """Issue a new token if the old one is still valid."""
    claims = verify_token(old_token, now=now)
    if claims is None:
        return None
    return create_token(claims["user_id"], claims["role"], now=now)
