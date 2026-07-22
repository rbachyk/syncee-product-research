"""Authentication flows (spec §7).

``login`` runs a headed browser for manual Syncee login and saves the session; ``validate``
loads the saved session and confirms authenticated marketplace content is reachable,
raising AUTH_SESSION_EXPIRED otherwise.

The page-state heuristics (:func:`classify_auth_state`) are pure and unit-tested. The exact
authenticated / login-redirect markers are *data*, calibrated after discovery (spec §8.4)
and configurable via ``syncee.auth`` — so a misfiring validate is fixed in YAML, not code.
Both flows wait for the page to finish loading and write diagnostics (URL, HTML, text,
screenshot) to ``artifacts/auth/`` so you can see exactly what the browser saw.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import AppConfig
from ..observability.errors import AuthError, ErrorCode, ScannerError
from ..observability.logging import get_logger
from .session import browser_context, save_session, session_exists

log = get_logger(__name__)


class AuthState(str, Enum):
    AUTHENTICATED = "authenticated"
    LOGIN_REQUIRED = "login_required"
    ACCESS_DENIED = "access_denied"
    UNKNOWN = "unknown"


@dataclass
class AuthMarkers:
    """Signals used to classify a page. Confirmed/tuned via ``syncee.auth`` (spec §8.4)."""

    login_url_fragments: tuple[str, ...] = ("/login", "/signin", "/sign-in", "/auth")
    access_denied_fragments: tuple[str, ...] = ("/403", "access-denied", "forbidden")
    authenticated_text_markers: tuple[str, ...] = ("Marketplace", "Add to import list")
    login_text_markers: tuple[str, ...] = ("Log in", "Sign in", "Password")


AUTH_MARKERS = AuthMarkers()


def markers_from_config(config: AppConfig) -> AuthMarkers:
    a = config.syncee.auth
    return AuthMarkers(
        login_url_fragments=tuple(a.login_url_fragments),
        access_denied_fragments=tuple(a.access_denied_fragments),
        authenticated_text_markers=tuple(a.authenticated_markers),
        login_text_markers=tuple(a.login_markers),
    )


def classify_auth_state(
    url: str, page_text: str, markers: AuthMarkers = AUTH_MARKERS
) -> AuthState:
    """Classify a loaded page as authenticated / login-required / denied (pure)."""
    low_url = (url or "").lower()
    low_text = page_text or ""

    if any(frag in low_url for frag in markers.access_denied_fragments):
        return AuthState.ACCESS_DENIED
    if any(frag in low_url for frag in markers.login_url_fragments):
        return AuthState.LOGIN_REQUIRED
    if any(m in low_text for m in markers.authenticated_text_markers):
        return AuthState.AUTHENTICATED
    if any(m in low_text for m in markers.login_text_markers):
        return AuthState.LOGIN_REQUIRED
    return AuthState.UNKNOWN


def _probe_url(config: AppConfig) -> str:
    """The authenticated-only page validate/login check (``syncee.auth.probe_path``)."""
    base = config.syncee.base_url.rstrip("/")
    path = config.syncee.auth.probe_path
    return base + (path if path.startswith("/") else "/" + path)


def _settle(page: object, config: AppConfig) -> None:
    """Wait for the page (often a JS SPA) to finish loading before reading text."""
    for state in ("domcontentloaded", "networkidle"):
        try:
            page.wait_for_load_state(state, timeout=config.syncee.browser_timeout_seconds * 1000)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best-effort
            pass
    time.sleep(config.syncee.auth.settle_seconds)


def _capture(page: object, subdir: str) -> Path:
    """Save URL, HTML, visible text and a screenshot for inspection. Returns the dir."""
    out = Path("artifacts/auth") / subdir
    out.mkdir(parents=True, exist_ok=True)
    url = _safe(lambda: page.url, "")  # type: ignore[attr-defined]
    (out / "url.txt").write_text(str(url), encoding="utf-8")
    (out / "page.html").write_text(_safe(lambda: page.content(), ""), encoding="utf-8")
    (out / "page_text.txt").write_text(_safe_text(page), encoding="utf-8")
    try:
        page.screenshot(path=str(out / "screenshot.png"), full_page=False)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    return out


def login(config: AppConfig, *, poll_seconds: float = 3.0, max_wait_seconds: int = 600) -> None:
    """Headed manual-login flow that saves the browser session (spec §7.1)."""
    markers = markers_from_config(config)
    probe = _probe_url(config)
    with browser_context(
        storage_state_path=config.syncee.storage_state_path,
        headless=False,
        timeout_seconds=config.syncee.browser_timeout_seconds,
        load_session=False,
    ) as context:
        page = context.new_page()
        page.goto(probe)
        log.info("auth.login_started", message="Complete login in the opened browser window")

        waited = 0.0
        while waited < max_wait_seconds:
            state = classify_auth_state(page.url, _safe_text(page), markers)
            if state is AuthState.AUTHENTICATED:
                save_session(context, config.syncee.storage_state_path)
                _capture(page, "login")
                log.info("auth.login_succeeded", url=page.url)
                return
            time.sleep(poll_seconds)
            waited += poll_seconds

        _capture(page, "login")
        raise ScannerError(
            ErrorCode.AUTH_SESSION_EXPIRED,
            "Timed out waiting for manual login (see artifacts/auth/login/).",
        )


def validate(config: AppConfig) -> AuthState:
    """Validate the saved session before a scan (spec §7.2). Raises AuthError if expired."""
    if not session_exists(config.syncee.storage_state_path):
        raise AuthError("No saved session; run `syncee-scanner auth login` first")

    markers = markers_from_config(config)
    probe = _probe_url(config)
    with browser_context(
        storage_state_path=config.syncee.storage_state_path,
        headless=config.syncee.headless,
        timeout_seconds=config.syncee.browser_timeout_seconds,
    ) as context:
        page = context.new_page()
        page.goto(probe)
        _settle(page, config)
        final_url = page.url
        state = classify_auth_state(final_url, _safe_text(page), markers)
        log.info("auth.validate_result", state=state.value, url=final_url)
        if state is not AuthState.AUTHENTICATED:
            artifact_dir = _capture(page, "validate")

    if state is AuthState.AUTHENTICATED:
        log.info("auth.validated", url=final_url)
        return state
    if state is AuthState.ACCESS_DENIED:
        raise ScannerError(
            ErrorCode.ACCESS_DENIED,
            f"Syncee access denied (url={final_url}; see {artifact_dir}/).",
        )
    raise AuthError(
        f"Session invalid or expired (state={state.value}, url={final_url}). "
        f"If you are actually logged in, the auth markers need calibrating: inspect "
        f"{artifact_dir}/ and set syncee.auth markers in config. Try `auth validate --headed`."
    )


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # pragma: no cover - defensive
        return default


def _safe_text(page: object) -> str:
    return _safe(lambda: page.inner_text("body"), "")  # type: ignore[attr-defined]
