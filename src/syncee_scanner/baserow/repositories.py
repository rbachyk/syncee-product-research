"""Idempotent upsert repositories (spec §16.4, §16.5, §37).

Implements the fingerprint-driven upsert sequence: classify each record as new / changed /
unchanged against the in-memory index, batch-create new rows, batch-update changed rows,
and lightly touch ``Last Seen At`` on unchanged rows. Repeated processing of the same page
never creates duplicates (spec §16.5).

Repositories are the *only* writers to Baserow (plan invariant), keeping business logic out
of the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .batching import batch_create_all, batch_update_all, dedupe_by_key
from .client import BaserowClient
from .indexes import KeyIndex


@dataclass
class UpsertRecord:
    """One record ready to upsert.

    Attributes:
        key: the stable application key (Supplier Key / Product Key).
        fields: the record body written on create and on changed-updates (must include
            the key field).
        fingerprint: deterministic fingerprint of tracked fields (spec §19.3).
        create_extra: fields applied *only* on create (e.g. First Seen At, Is New=True) so
            they are never overwritten on later updates.
        changed_extra: fields applied *only* on a changed-update (e.g. Last Changed At,
            Is New=False).
        touch_fields: minimal payload applied to *unchanged* rows (e.g. Last Seen At,
            Last Scan Run) so freshness is recorded without a full rewrite.
    """

    key: str
    fields: dict
    fingerprint: str
    create_extra: dict = field(default_factory=dict)
    changed_extra: dict = field(default_factory=dict)
    touch_fields: dict = field(default_factory=dict)


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    key_to_row_id: dict[str, int] = field(default_factory=dict)
    changed_keys: list[str] = field(default_factory=list)

    @property
    def seen(self) -> int:
        return self.created + self.updated + self.unchanged


def upsert_records(
    client: BaserowClient,
    table_id: str | int,
    *,
    key_field: str,
    fingerprint_field: str,
    records: list[UpsertRecord],
    index: KeyIndex,
    batch_size: int,
    dry_run: bool = False,
) -> UpsertResult:
    """Classify and persist records idempotently (spec §16.4).

    The ``index`` is updated in place with created rows so later pages in the same run see
    them (idempotency, spec §16.5). In ``dry_run`` mode nothing is written but the result
    counts reflect what *would* happen.
    """
    # Collapse intra-batch duplicates first (idempotency within a page).
    deduped = dedupe_by_key(
        ({"__rec": r, key_field: r.key} for r in records), key_field
    )
    unique_records = [item["__rec"] for item in deduped]

    to_create: list[dict] = []
    create_keys: list[str] = []
    to_update: list[dict] = []
    result = UpsertResult()

    for rec in unique_records:
        entry = index.get(rec.key)
        if entry is None:
            payload = {**rec.fields, **rec.create_extra}
            payload[fingerprint_field] = rec.fingerprint
            to_create.append(payload)
            create_keys.append(rec.key)
        elif entry.fingerprint != rec.fingerprint:
            payload = {**rec.fields, **rec.changed_extra}
            payload["id"] = entry.row_id
            payload[fingerprint_field] = rec.fingerprint
            to_update.append(payload)
            result.updated += 1
            result.changed_keys.append(rec.key)
            result.key_to_row_id[rec.key] = entry.row_id
            index.add(rec.key, entry.row_id, rec.fingerprint)
        else:
            result.unchanged += 1
            result.key_to_row_id[rec.key] = entry.row_id
            if rec.touch_fields:
                to_update.append({"id": entry.row_id, **rec.touch_fields})

    if dry_run:
        result.created = len(to_create)
        for offset, key in enumerate(create_keys):
            # No real IDs in dry-run; use negative placeholders so linking is visible.
            result.key_to_row_id[key] = -(offset + 1)
        return result

    created_rows = batch_create_all(client, table_id, to_create, batch_size=batch_size)
    for key, row in zip(create_keys, created_rows, strict=False):
        row_id = row["id"]
        result.key_to_row_id[key] = row_id
        index.add(key, row_id, _fingerprint_of(row, fingerprint_field))
    result.created = len(created_rows)

    if to_update:
        batch_update_all(client, table_id, to_update, batch_size=batch_size)

    return result


def _fingerprint_of(row: dict, fingerprint_field: str) -> str | None:
    return row.get(fingerprint_field)
