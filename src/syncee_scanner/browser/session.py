"""Playwright storage-state session management (spec §7).

Persists and restores the authenticated browser session at ``data/auth/storage_state.json``
(spec §7.1). The session file is git-ignored, never logged, and never uploaded to Baserow.
Playwright is imported lazily so the rest of the package (config, scoring, Baserow) imports
without the browser dependency installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..observability.logging import get_logger

log = get_logger(__name__)


def session_exists(storage_state_path: str | Path) -> bool:
    return Path(storage_state_path).is_file()


@contextmanager
def browser_context(
    *,
    storage_state_path: str | Path,
    headless: bool = True,
    timeout_seconds: int = 60,
    load_session: bool = True,
) -> Iterator[object]:
    """Yield a Playwright browser context, restoring the saved session if present.

    Playwright is imported here (not at module load) so unit tests and the Baserow/scoring
    code never require the browser binary.
    """
    from playwright.sync_api import sync_playwright  # lazy import

    path = Path(storage_state_path)
    state = str(path) if (load_session and path.is_file()) else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=state)
        context.set_default_timeout(timeout_seconds * 1000)
        log.debug("browser.context_opened", headless=headless, restored=bool(state))
        try:
            yield context
        finally:
            context.close()
            browser.close()


def save_session(context: object, storage_state_path: str | Path) -> Path:
    """Persist the current browser session to disk (spec §7.1)."""
    path = Path(storage_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))  # type: ignore[attr-defined]
    log.info("browser.session_saved", path=str(path))
    return path
