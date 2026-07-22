"""Batch helpers for Baserow writes (spec §16.3, §16.4)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

from .client import BaserowClient

T = TypeVar("T")


def chunked(items: list[T], size: int) -> Iterator[list[T]]:
    """Yield ``items`` in lists of at most ``size`` (size must be >= 1)."""
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    for i in range(0, len(items), size):
        yield items[i : i + size]


def batch_create_all(
    client: BaserowClient, table_id: str | int, rows: list[dict], *, batch_size: int
) -> list[dict]:
    """Create many rows, chunked to ``batch_size``. Returns created rows in order."""
    created: list[dict] = []
    for chunk in chunked(rows, batch_size):
        created.extend(client.batch_create(table_id, chunk))
    return created


def batch_update_all(
    client: BaserowClient, table_id: str | int, rows: list[dict], *, batch_size: int
) -> list[dict]:
    """Update many rows (each must include ``id``), chunked to ``batch_size``."""
    updated: list[dict] = []
    for chunk in chunked(rows, batch_size):
        updated.extend(client.batch_update(table_id, chunk))
    return updated


def dedupe_by_key(records: Iterable[dict], key_field: str) -> list[dict]:
    """Collapse records sharing a key, last-wins (idempotency within a page, §16.5)."""
    seen: dict[str, dict] = {}
    for rec in records:
        key = rec.get(key_field)
        if key is not None:
            seen[key] = rec
    return list(seen.values())
