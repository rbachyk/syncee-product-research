"""Generic REST-API page transport for API-based sources (CJ, BigBuy, …).

Fits the same callable interface as ``browser.transport.SynceeApiTransport`` — given the
per-page request payload the source builds, it performs one authenticated HTTP request and
returns the raw JSON dict for the mapper to parse. Auth is either a static token from an env
var, or a *token exchange* (CJ mints a short-lived access token from email + key), cached to
a file so it isn't re-minted every run. Endpoint/paths/pagination all come from the source's
mapping YAML, so wiring a new API source is config, not code.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ..config import RestApiConfig
from ..observability.errors import ErrorCode, ScannerError


def _dig(obj, path: str):
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


class RestApiTransport:
    def __init__(self, api: RestApiConfig, mapping, *, timeout: float = 30.0,
                 env: dict[str, str] | None = None) -> None:
        self.api = api
        self.mapping = mapping
        self._env = env if env is not None else __import__("os").environ
        self._client = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._last_request: float = 0.0

    def _throttle(self) -> None:
        """Respect the API's request-rate limit (CJ: 1 request/second)."""
        gap = self.api.min_interval_seconds
        if gap <= 0:
            return
        wait = gap - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    # --- auth --------------------------------------------------------------------------

    def _access_token(self) -> str:
        """Static token from env, or a cached/minted exchange token (CJ)."""
        if not self.api.auth_url:  # simple key-in-header APIs
            token = self._env.get(self.api.auth_env or "")
            if not token:
                raise ScannerError(
                    ErrorCode.CONFIGURATION_ERROR,
                    f"{self.api.auth_env} is not set — required for this source's API.",
                )
            return token
        if self._token:
            return self._token
        cached = self._read_token_cache()
        if cached:
            self._token = cached
            return cached
        self._token = self._mint_token()
        return self._token

    def _read_token_cache(self) -> str | None:
        if not self.api.token_cache:
            return None
        try:
            data = json.loads(Path(self.api.token_cache).read_text())
        except (OSError, ValueError):
            return None
        if data.get("expires_at", 0) > time.time():
            return data.get("token")
        return None

    def _mint_token(self) -> str:
        email = self._env.get(self.api.auth_email_env or "")
        password = self._env.get(self.api.auth_env or "")
        if not (email and password):
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                f"{self.api.auth_email_env} and {self.api.auth_env} are required to mint a token.",
            )
        try:
            resp = self._client.post(self.api.auth_url, json={"email": email, "password": password})
        except httpx.HTTPError as exc:
            raise ScannerError(ErrorCode.SOURCE_API_ERROR, f"token exchange failed: {exc}") from exc
        token = _dig(resp.json(), self.api.token_path)
        if not token:
            raise ScannerError(
                ErrorCode.SOURCE_API_ERROR,
                f"token exchange returned no token at '{self.api.token_path}': {resp.text[:160]}",
            )
        if self.api.token_cache:
            try:
                Path(self.api.token_cache).parent.mkdir(parents=True, exist_ok=True)
                Path(self.api.token_cache).write_text(json.dumps({
                    "token": token, "expires_at": time.time() + self.api.token_ttl_hours * 3600,
                }))
            except OSError:
                pass
        return token

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.api.extra_headers}
        if self.api.auth_header:
            headers[self.api.auth_header] = self._access_token()
        return headers

    # --- fetch -------------------------------------------------------------------------

    def __call__(self, payload: dict) -> dict:
        """One page request. ``payload`` is the request body/params the source built for this
        page (page/size/category); the URL + method come from the list mapping."""
        url = self.mapping.list.endpoint_template
        if not url:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                "This source's mapping has no list.endpoint_template — run discovery first.",
            )
        method = (self.mapping.list.method or "GET").upper()
        self._throttle()
        try:
            if method == "GET":
                resp = self._client.get(url, headers=self._headers(), params=payload)
            else:
                resp = self._client.request(method, url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ScannerError(ErrorCode.SOURCE_API_ERROR, f"{url} request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ScannerError(
                ErrorCode.SOURCE_API_ERROR, f"{url} returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def get_detail(self, product_id) -> dict | None:
        """Fetch one product's detail (for enrichment) via ``list.detail_endpoint_template``."""
        tmpl = self.mapping.list.detail_endpoint_template
        if not tmpl:
            return None
        url = tmpl.replace("{id}", str(product_id))
        self._throttle()
        try:
            resp = self._client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ScannerError(ErrorCode.SOURCE_API_ERROR, f"{url} failed: {exc}") from exc
        if resp.status_code != 200:
            return None
        body = resp.json()
        path = self.mapping.list.detail_path  # unwrap the product object (CJ: under "data")
        detail = _dig(body, path) if path else body
        if isinstance(detail, dict):
            self._attach_stock(detail)
        return detail

    def _attach_stock(self, detail: dict) -> None:
        """Fetch real inventory (a separate per-variant call on CJ) and inject the total."""
        lm = self.mapping.list
        if not (lm.stock_endpoint_template and lm.stock_vid_path):
            return
        from .mapper import get_path  # supports numeric indices (variants.0.vid)
        vid = get_path(detail, lm.stock_vid_path)
        if not vid:
            return
        url = lm.stock_endpoint_template.replace("{vid}", str(vid))
        self._throttle()
        try:
            resp = self._client.get(url, headers=self._headers())
        except httpx.HTTPError:
            return
        if resp.status_code != 200:
            return
        rows = _dig(resp.json(), lm.stock_response_path) if lm.stock_response_path else resp.json()
        if isinstance(rows, list) and lm.stock_sum_field:
            total = sum(r.get(lm.stock_sum_field) or 0 for r in rows if isinstance(r, dict))
            detail[lm.stock_target_field] = total

    def close(self) -> None:
        self._client.close()
