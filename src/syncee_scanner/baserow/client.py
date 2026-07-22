"""Baserow REST client (spec §16).

A thin, synchronous httpx wrapper around the Baserow database-token API. It provides:

  * retry with backoff on transient HTTP failures (spec §16.3 / §34.1);
  * field name -> field ID resolution so the scanner references stable IDs (spec §16.2);
  * paginated row listing and batch create/update (spec §16.4).

Auth failures map to BASEROW_AUTH_ERROR, other API failures to BASEROW_API_ERROR, and
schema drift is surfaced by the repository/validation layer as BASEROW_SCHEMA_MISMATCH.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..observability.errors import BaserowAuthError, BaserowError
from ..observability.logging import get_logger

log = get_logger(__name__)


class BaserowClient:
    def __init__(
        self,
        api_url: str,
        database_token: str,
        *,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        timeout: float = 90.0,  # Products table is large; page reads at high offsets are slow
        client: httpx.Client | None = None,
    ) -> None:
        if not database_token:
            raise BaserowAuthError("BASEROW_DATABASE_TOKEN is not set")
        self.api_url = api_url.rstrip("/")
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Token {database_token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BaserowClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Low-level request with retry ---------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.api_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:  # network/timeout -> transient
                last_exc = exc
                self._sleep(attempt, reason=str(exc))
                continue

            if resp.status_code in (401, 403):
                raise BaserowAuthError(
                    f"Baserow auth failed ({resp.status_code}) for {method} {path}"
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = BaserowError(
                    f"Baserow transient error {resp.status_code} for {method} {path}"
                )
                self._sleep(attempt, reason=f"HTTP {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise BaserowError(
                    f"Baserow error {resp.status_code} for {method} {path}: {resp.text[:500]}",
                    context={"status": resp.status_code},
                )
            return resp

        raise BaserowError(
            f"Baserow request failed after {self.max_retries} attempts: {method} {path}",
            context={"cause": str(last_exc)},
        )

    def _sleep(self, attempt: int, *, reason: str) -> None:
        if attempt >= self.max_retries:
            return
        delay = self.retry_backoff_seconds * attempt
        log.warning("baserow.retry", attempt=attempt, delay=delay, reason=reason)
        time.sleep(delay)

    # --- Fields --------------------------------------------------------------------

    def list_fields(self, table_id: str | int) -> list[dict]:
        """Return field metadata for a table (spec §16.2 name->id resolution)."""
        resp = self._request("GET", f"/api/database/fields/table/{table_id}/")
        return resp.json()

    def field_map(self, table_id: str | int) -> dict[str, dict]:
        """Return {field_name: field_metadata} for a table."""
        return {f["name"]: f for f in self.list_fields(table_id)}

    # --- Rows ----------------------------------------------------------------------

    def iter_rows(
        self,
        table_id: str | int,
        *,
        page_size: int = 200,
        user_field_names: bool = True,
        include: str | None = None,
    ) -> Iterator[dict]:
        """Yield all rows in a table, following pagination."""
        page = 1
        while True:
            params: dict[str, Any] = {
                "size": page_size,
                "page": page,
                "user_field_names": str(user_field_names).lower(),
            }
            if include:
                params["include"] = include
            resp = self._request(
                "GET", f"/api/database/rows/table/{table_id}/", params=params
            )
            data = resp.json()
            yield from data.get("results", [])
            if not data.get("next"):
                break
            page += 1

    def batch_create(
        self, table_id: str | int, items: list[dict], *, user_field_names: bool = True
    ) -> list[dict]:
        """Create multiple rows in one request (spec §16.4)."""
        if not items:
            return []
        resp = self._request(
            "POST",
            f"/api/database/rows/table/{table_id}/batch/",
            params={"user_field_names": str(user_field_names).lower()},
            json={"items": [_drop_nulls(i) for i in items]},
        )
        return resp.json().get("items", [])

    def batch_update(
        self, table_id: str | int, items: list[dict], *, user_field_names: bool = True
    ) -> list[dict]:
        """Update multiple rows in one request; each item must include ``id``."""
        if not items:
            return []
        resp = self._request(
            "PATCH",
            f"/api/database/rows/table/{table_id}/batch/",
            params={"user_field_names": str(user_field_names).lower()},
            json={"items": [_drop_nulls(i) for i in items]},
        )
        return resp.json().get("items", [])

    def upload_file(
        self, content: bytes, filename: str, *, content_type: str = "image/jpeg"
    ) -> dict:
        """Upload a file to Baserow's user-file store; returns its file object.

        The returned ``name`` is what a File field value references, e.g.
        ``{"Processed Image": [{"name": obj["name"]}]}`` (spec §11 publish-prep).
        """
        resp = self._request(
            "POST",
            "/api/user-files/upload-file/",
            files={"file": (filename, content, content_type)},
        )
        return resp.json()


def _drop_nulls(item: dict) -> dict:
    """Omit None values from a write payload.

    Baserow rejects null for non-nullable fields (e.g. booleans must be true/false). Omitting
    a field lets Baserow use its default on create and keep the existing value on update — the
    scanner never needs to explicitly null a field.
    """
    return {k: v for k, v in item.items() if v is not None}
