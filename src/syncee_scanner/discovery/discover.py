"""Live Syncee discovery driver (spec §8).

Drives an authenticated browser over the marketplace, records **all** JSON XHR/GraphQL
responses (not a keyword-filtered subset), navigates into a real product listing, and dumps
every response body so the actual product-list API can be identified. Output goes to
``artifacts/discovery/`` for a human to confirm the Discovery Gate (spec §8.4) before the
live ``SynceeSource`` mapping is finalized.

Discovery is deliberately exploratory: it captures evidence rather than asserting a final
schema.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ..browser.network import ResponseRecorder
from ..browser.session import browser_context
from ..config import AppConfig
from ..observability.logging import get_logger
from .report import DiscoveryFindings, write_discovery_artifacts

log = get_logger(__name__)

# URL substrings that *rank* an endpoint as a likely data API (not a capture filter).
_API_HINTS = ("/api/", "/graphql", "product", "supplier", "search", "catalog", "marketplace")

# Nav link texts to try when navigating from the marketplace home into a product listing.
_LISTING_LINKS = ("All Products", "New Products", "Home & Garden")

_MAX_RESPONSE_FILES = 300


def run_discovery(
    config: AppConfig,
    *,
    output_dir: str | Path = "artifacts/discovery",
    scrolls: int = 6,
    target_url: str | None = None,
) -> DiscoveryFindings:
    """Run discovery against live Syncee and write artifacts. Requires a saved session."""
    recorder = ResponseRecorder()  # capture everything; rank later
    findings = DiscoveryFindings()
    base = config.syncee.base_url.rstrip("/")
    out = Path(output_dir)

    with browser_context(
        storage_state_path=config.syncee.storage_state_path,
        headless=config.syncee.headless,
        timeout_seconds=config.syncee.browser_timeout_seconds,
    ) as context:
        page = context.new_page()
        recorder.attach(page)

        page.goto(target_url or (base + "/"), wait_until="domcontentloaded")
        _wait(page, config)
        findings.routes["landing"] = page.url
        _screenshot(page, out, "landing.png")

        _dismiss_cookiebanner(page)

        # Navigate into a real product listing (unless an explicit target_url was given).
        if not target_url:
            findings.routes["catalog"] = _goto_listing(page, config)
        else:
            findings.routes["catalog"] = page.url

        # Trigger lazy/paginated product loads.
        for _ in range(scrolls):
            try:
                page.mouse.wheel(0, 5000)
            except Exception:  # pragma: no cover
                break
            page.wait_for_timeout(int(config.syncee.page_delay_seconds * 1000))

        _screenshot(page, out, "catalog.png")
        findings.routes["current"] = page.url

    # Persist every JSON response body for inspection + summarize endpoints.
    written = _dump_responses(recorder, out)
    findings.network_endpoints = _summarize_endpoints(recorder)
    findings.notes.append(f"Wrote {written} JSON response bodies to {out}/responses/.")

    list_resp = _pick_product_list(recorder)
    if list_resp is not None:
        findings.sample_product_list_response = list_resp.body
        findings.pagination["observed_list_endpoint"] = list_resp.url
        findings.notes.append(
            "Candidate product-list endpoint auto-selected (largest object array). "
            "Confirm it, then set list.endpoint_template + paths in config/syncee_mapping.yaml."
        )
    else:
        findings.notes.append(
            "No JSON response with an object array was captured. Open responses/ to find the "
            "product API, or re-run with --url pointing at the category listing page."
        )

    write_discovery_artifacts(findings, output_dir=out)
    log.info(
        "discovery.completed", output=str(out), endpoints=len(findings.network_endpoints),
        json_responses=written, catalog=findings.routes.get("catalog"),
    )
    return findings


# --- helpers -----------------------------------------------------------------------


def _wait(page, config: AppConfig) -> None:
    # A busy SPA may never go fully idle — wait briefly for DOM, then give network a short
    # window, but don't block on it.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:  # pragma: no cover
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:  # pragma: no cover
        pass
    page.wait_for_timeout(int(config.syncee.page_delay_seconds * 1000))


def _dismiss_cookiebanner(page) -> None:
    for name in ("Accept", "Accept all", "I agree", "Got it"):
        try:
            page.get_by_role("button", name=name).first.click(timeout=2500)
            log.info("discovery.cookiebanner_dismissed", button=name)
            return
        except Exception:
            continue


def _goto_listing(page, config: AppConfig) -> str:
    """Click a nav link into a product listing; return the resulting URL."""
    for name in _LISTING_LINKS:
        try:
            page.get_by_role("link", name=name, exact=False).first.click(timeout=5000)
            _wait(page, config)
            log.info("discovery.listing_opened", via=name, url=page.url)
            return page.url
        except Exception:
            continue
    log.warning("discovery.listing_not_found", tried=_LISTING_LINKS)
    return page.url


def _dump_responses(recorder: ResponseRecorder, out: Path) -> int:
    """Write each captured JSON response body to responses/NNN_<host><path>.json."""
    import json

    folder = out / "responses"
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    for r in recorder.responses:
        if r.body is None or written >= _MAX_RESPONSE_FILES:
            continue
        parts = urlsplit(r.url)
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{parts.netloc}{parts.path}")[:80]
        path = folder / f"{written:03d}_{r.method}_{slug}.json"
        try:
            path.write_text(
                json.dumps(
                    {"url": r.url, "method": r.method, "status": r.status,
                     "request_body": r.request_body, "body": r.body},
                    indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            written += 1
        except Exception:  # pragma: no cover
            continue
    return written


def _summarize_endpoints(recorder: ResponseRecorder) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in recorder.responses:
        parts = urlsplit(r.url)
        endpoint = f"{parts.scheme}://{parts.netloc}{parts.path}"
        key = f"{r.method} {endpoint}"
        if key not in seen:
            seen[key] = {
                "method": r.method,
                "endpoint": endpoint,
                "status": r.status,
                "resource_type": r.resource_type,
                "has_json_body": r.body is not None,
                "likely_data_api": any(h in r.url.lower() for h in _API_HINTS)
                and r.body is not None,
                "sample_query": parts.query[:200],
            }
    # Data-API candidates first for easy scanning.
    return sorted(seen.values(), key=lambda e: not e["likely_data_api"])


def _largest_array(obj, depth: int = 0) -> int:
    """Return the size of the largest list-of-dicts found anywhere in a JSON value."""
    if depth > 6:
        return 0
    best = 0
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            best = len(obj)
        for item in obj[:50]:
            best = max(best, _largest_array(item, depth + 1))
    elif isinstance(obj, dict):
        for value in obj.values():
            best = max(best, _largest_array(value, depth + 1))
    return best


def _pick_product_list(recorder: ResponseRecorder):
    """Heuristically pick the response most likely to be the product list."""
    best = None
    best_size = 0
    for r in recorder.responses:
        if r.body is None:
            continue
        size = _largest_array(r.body)
        # Prefer hinted URLs on ties by giving them a small boost.
        if any(h in r.url.lower() for h in _API_HINTS):
            size += 1
        if size > best_size:
            best_size, best = size, r
    return best if best_size >= 2 else None


def _screenshot(page, output_dir: str | Path, name: str) -> None:
    try:
        path = Path(output_dir) / "screenshots" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=False)
    except Exception:  # pragma: no cover
        log.warning("discovery.screenshot_failed", name=name)
