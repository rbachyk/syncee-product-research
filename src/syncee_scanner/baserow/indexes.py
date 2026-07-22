"""Lightweight in-memory key indexes (spec §16.4).

At the start of a run the scanner loads compact ``key -> (row_id, fingerprint)`` maps for
suppliers and products so that upserts never query Baserow per record (spec §16.4). Only
the few fields needed for matching are pulled, not full rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import BaserowClient


@dataclass
class IndexEntry:
    row_id: int
    fingerprint: str | None = None


class KeyIndex:
    """A ``key -> IndexEntry`` map with helpers for classifying records."""

    def __init__(self, entries: dict[str, IndexEntry] | None = None) -> None:
        self._entries: dict[str, IndexEntry] = entries or {}

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> IndexEntry | None:
        return self._entries.get(key)

    def row_id(self, key: str) -> int | None:
        entry = self._entries.get(key)
        return entry.row_id if entry else None

    def add(self, key: str, row_id: int, fingerprint: str | None = None) -> None:
        self._entries[key] = IndexEntry(row_id=row_id, fingerprint=fingerprint)

    def keys(self) -> set[str]:
        return set(self._entries)


def load_key_index(
    client: BaserowClient,
    table_id: str | int,
    *,
    key_field: str,
    fingerprint_field: str = "Record Fingerprint",
    page_size: int = 200,
) -> KeyIndex:
    """Load a compact key index for a table (spec §16.4)."""
    entries: dict[str, IndexEntry] = {}
    for row in client.iter_rows(table_id, page_size=page_size, user_field_names=True):
        key = row.get(key_field)
        if not key:
            continue
        entries[key] = IndexEntry(
            row_id=row["id"],
            fingerprint=row.get(fingerprint_field),
        )
    return KeyIndex(entries)
