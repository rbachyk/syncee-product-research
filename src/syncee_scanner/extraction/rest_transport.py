"""Generic REST-API page transport for API-based sources (CJ, BigBuy, …).

Fits the same callable interface as ``browser.transport.SynceeApiTransport`` — given the
per-page request payload the source builds, it performs one authenticated HTTP request and
returns the raw JSON dict for the mapper to parse. The auth token is read from an env var
(never hard-coded); the endpoint/paths/pagination all come from the source's mapping YAML,
so wiring a new API source is config, not code.
"""

from __future__ import annotations

import os

import httpx

from ..config import RestApiConfig
from ..observability.errors import ErrorCode, ScannerError


class RestApiTransport:
    def __init__(self, api: RestApiConfig, mapping, *, timeout: float = 30.0) -> None:
        self.api = api
        self.mapping = mapping
        self._client = httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.api.extra_headers}
        if self.api.auth_header and self.api.auth_env:
            token = os.environ.get(self.api.auth_env)
            if not token:
                raise ScannerError(
                    ErrorCode.CONFIGURATION_ERROR,
                    f"{self.api.auth_env} is not set — required for this source's API.",
                )
            headers[self.api.auth_header] = token
        return headers

    def __call__(self, payload: dict) -> dict:
        """One page request. ``payload`` is the request body/params the source built for this
        page (offset/size/category, etc.); the URL + method come from the list mapping."""
        url = self.mapping.list.endpoint_template
        if not url:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                "This source's mapping has no list.endpoint_template — run discovery first.",
            )
        method = (self.mapping.list.method or "GET").upper()
        try:
            if method == "GET":
                resp = self._client.get(url, headers=self._headers(), params=payload)
            else:
                resp = self._client.request(
                    method, url, headers=self._headers(), json=payload
                )
        except httpx.HTTPError as exc:  # network failure — retryable
            raise ScannerError(ErrorCode.SOURCE_API_ERROR, f"{url} request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ScannerError(
                ErrorCode.SOURCE_API_ERROR,
                f"{url} returned {resp.status_code}: {resp.text[:200]}",
            )
        return resp.json()

    def get_detail(self, product_id) -> dict | None:
        """Fetch one product's detail (for enrichment) via ``list.detail_endpoint_template``."""
        tmpl = self.mapping.list.detail_endpoint_template
        if not tmpl:
            return None
        url = tmpl.replace("{id}", str(product_id))
        try:
            resp = self._client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ScannerError(ErrorCode.SOURCE_API_ERROR, f"{url} failed: {exc}") from exc
        if resp.status_code != 200:
            return None
        return resp.json()

    def close(self) -> None:
        self._client.close()
