"""Self-contained session-cookie auth for the dashboard (no external deps).

A single operator credential comes from the environment; a successful login sets an
HMAC-signed, expiring cookie (stdlib only — no itsdangerous / JWT lib). If
``DASHBOARD_PASSWORD`` is unset the dashboard stays open, so local dev and setups that
already sit behind a trusted reverse proxy keep working unchanged.

Env:
  DASHBOARD_PASSWORD        enable login; the operator password (required to turn auth on)
  DASHBOARD_USERNAME        login name (default "admin")
  DASHBOARD_SECRET          cookie-signing key; if unset, derived from the password so it's
                            stable across restarts but rotates when the password changes
  DASHBOARD_COOKIE_SECURE   "1"/"true" to force the Secure flag (set when served over HTTPS)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE_NAME = "rbhome_session"
SESSION_TTL = 60 * 60 * 24 * 14  # 14 days


def _password() -> str | None:
    return os.environ.get("DASHBOARD_PASSWORD") or None


def _username() -> str:
    return os.environ.get("DASHBOARD_USERNAME", "admin")


def auth_enabled() -> bool:
    """Login is enforced only when an operator password is configured."""
    return _password() is not None


def cookie_secure() -> bool:
    return os.environ.get("DASHBOARD_COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def _secret() -> bytes:
    explicit = os.environ.get("DASHBOARD_SECRET")
    if explicit:
        return explicit.encode()
    # Deterministic fallback tied to the password: stable across restarts, rotates on change.
    return hashlib.sha256(f"rbhome-dash:{_password() or ''}".encode()).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(username: str) -> str:
    """Return a signed ``payload.signature`` session token."""
    payload = json.dumps(
        {"u": username, "exp": int(time.time()) + SESSION_TTL}, separators=(",", ":")
    ).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify_token(token: str | None) -> str | None:
    """Return the username if the token is well-formed, correctly signed, and unexpired."""
    if not token or "." not in token:
        return None
    data_b64, sig_b64 = token.rsplit(".", 1)
    try:
        data, sig = _b64d(data_b64), _b64d(sig_b64)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(_secret(), data, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload.get("u")


def check_credentials(username: str, password: str) -> bool:
    """Constant-time check of a login attempt against the configured credential."""
    pw = _password()
    if pw is None:
        return False
    ok_user = hmac.compare_digest(username or "", _username())
    ok_pass = hmac.compare_digest(password or "", pw)
    return ok_user and ok_pass


def safe_next(target: str | None) -> str:
    """Constrain post-login redirects to local paths (no open-redirect via ``next``)."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target
