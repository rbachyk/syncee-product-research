"""Live Syncee page transport (spec §5.4, §8.4).

Fetches raw Syncee list responses through Playwright's authenticated request context, which
carries the saved session cookies (spec §7.1) — discovery confirmed the product-search API
authenticates by cookie, no token needed. Preferring the structured API over DOM scraping
follows the extraction priority in spec §5.4.

:class:`SynceeApiTransport` POSTs the offset-paginated search body (the confirmed
``syncee-product-service/products/search`` API). The HTTP call runs only against a real
authenticated session; the request-body assembly is done in
:meth:`~..extraction.source.SynceeSource._iter_offset` and is unit-tested with a fake
transport.
"""

from __future__ import annotations

import json

from ..config import AppConfig
from ..extraction.mapper import SynceeMapping
from ..observability.errors import ConfigurationError
from ..observability.logging import get_logger

log = get_logger(__name__)


class SynceeApiTransport:
    """Callable ``transport(payload: dict) -> dict`` backed by ``context.request`` (cookies).

    Opens the browser lazily on first call and is closed by :class:`SynceeSource` when the
    page generator finishes (or via :meth:`close`).
    """

    def __init__(self, config: AppConfig, mapping: SynceeMapping) -> None:
        if not mapping.list.endpoint_template:
            raise ConfigurationError(
                "config/syncee_mapping.yaml has no list.endpoint_template. Run "
                "`syncee-scanner discover`, confirm the search endpoint (spec §8.4), and set it."
            )
        self.config = config
        self.endpoint = mapping.list.endpoint_template
        self.method = mapping.list.method.upper()
        self.detail_endpoint = mapping.list.detail_endpoint_template
        self.origin = _origin(config.syncee.base_url)
        self._pw = None
        self._browser = None
        self._context = None

    def _ensure_open(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright  # lazy import

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.config.syncee.headless)
        self._context = self._browser.new_context(
            storage_state=self.config.syncee.storage_state_path
        )

    def __call__(self, payload: dict) -> dict:  # pragma: no cover - requires live browser
        self._ensure_open()
        headers = {
            "content-type": "application/json",
            "origin": self.origin,
            "referer": self.origin + "/",
        }
        if self.method == "POST":
            resp = self._context.request.post(
                self.endpoint, data=json.dumps(payload), headers=headers,
                timeout=self.config.syncee.browser_timeout_seconds * 1000,
            )
        else:
            resp = self._context.request.get(
                self.endpoint, headers=headers,
                timeout=self.config.syncee.browser_timeout_seconds * 1000,
            )
        log.debug("transport.fetch", status=resp.status, offset=payload.get("from"))
        return resp.json()

    def get_detail(self, product_id: str) -> dict | None:  # pragma: no cover - live browser
        """GET the product-detail response for a product ID (cookie-authenticated)."""
        if not self.detail_endpoint:
            raise ConfigurationError("No list.detail_endpoint_template configured for enrichment.")
        self._ensure_open()
        url = self.detail_endpoint.replace("{id}", str(product_id))
        resp = self._context.request.get(
            url,
            headers={"origin": self.origin, "referer": self.origin + "/"},
            timeout=self.config.syncee.browser_timeout_seconds * 1000,
        )
        if resp.status != 200:
            log.warning("transport.detail_failed", status=resp.status, product_id=product_id)
            return None
        return resp.json()

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._browser = self._pw = self._context = None


def _origin(base_url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"
