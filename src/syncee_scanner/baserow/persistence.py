"""Baserow-backed scan persistence (spec §12, §16.4).

Implements the :class:`~..runs.persistence.ScanPersistence` protocol against live Baserow:
loads key indexes, writes the Scan Runs row and its checkpoints, and performs idempotent
supplier/product upserts through :func:`~.repositories.upsert_records`.
"""

from __future__ import annotations

from typing import Any

from ..extraction.normalization import now_iso
from ..models import CompletenessStatus, RunStatus, RunType
from ..observability.logging import get_logger
from ..runs.checkpoints import Checkpoint
from ..runs.manager import RunCounts, RunHandle, new_run_id
from .batching import batch_update_all
from .client import BaserowClient
from .indexes import KeyIndex, load_key_index
from .repositories import UpsertRecord, UpsertResult, upsert_records
from .schemas import (
    PRODUCTS,
    SCAN_RUNS,
    SUPPLIERS,
    T_MANUAL_DECISIONS,
    T_PRODUCT_CHANGES,
    T_PRODUCTS,
    T_SCAN_RUNS,
    T_SELECTION_BATCHES,
    T_SUPPLIERS,
    FieldType,
)

log = get_logger(__name__)


def _number_fields(table) -> set[str]:
    return {f.name for f in table.fields if f.type == FieldType.NUMBER}


_SUPPLIER_NUMS = _number_fields(SUPPLIERS)
_PRODUCT_NUMS = _number_fields(PRODUCTS)
_SCAN_RUN_NUMS = _number_fields(SCAN_RUNS)


def _decimals(table) -> dict[str, int]:
    return {f.name: f.number_decimals for f in table.fields if f.type == FieldType.NUMBER}


_SUPPLIER_DECIMALS = _decimals(SUPPLIERS)
_PRODUCT_DECIMALS = _decimals(PRODUCTS)


def _round_fields(fields: dict, decimals: dict[str, int]) -> dict:
    """Round number fields to the schema's decimal places (Baserow enforces per-field)."""
    out = dict(fields)
    for name, places in decimals.items():
        v = out.get(name)
        if isinstance(v, float):
            out[name] = round(v, places)
    return out


def _round_records(records, decimals: dict[str, int]):
    for rec in records:
        rec.fields = _round_fields(rec.fields, decimals)
        rec.create_extra = _round_fields(rec.create_extra, decimals)
        rec.changed_extra = _round_fields(rec.changed_extra, decimals)
        rec.touch_fields = _round_fields(rec.touch_fields, decimals)
    return records


class BaserowPersistence:
    def __init__(
        self,
        client: BaserowClient,
        table_ids: dict[str, str | int],
        *,
        create_batch_size: int = 100,
        update_batch_size: int = 100,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.table_ids = table_ids
        self.create_batch_size = create_batch_size
        self.batch_size = max(create_batch_size, update_batch_size)
        self.dry_run = dry_run

    def _tid(self, name: str) -> str | int:
        return self.table_ids[name]

    def load_supplier_index(self) -> KeyIndex:
        return load_key_index(self.client, self._tid(T_SUPPLIERS), key_field="Supplier Key")

    def load_product_index(self) -> KeyIndex:
        return load_key_index(self.client, self._tid(T_PRODUCTS), key_field="Product Key")

    def create_run(self, *, run_type: RunType, category, config_hash, scanner_version) -> RunHandle:
        run_id = new_run_id(run_type)
        if self.dry_run:
            return RunHandle(run_id=run_id, row_id=None)
        rows = self.client.batch_create(
            self._tid(T_SCAN_RUNS),
            [
                {
                    "Run ID": run_id,
                    "Run Type": run_type.value,
                    "Status": RunStatus.RUNNING.value,
                    "Started At": now_iso(),
                    "Category": category,
                    "Configuration Hash": config_hash,
                    "Scanner Version": scanner_version,
                    "Completeness Status": CompletenessStatus.UNKNOWN.value,
                }
            ],
        )
        return RunHandle(run_id=run_id, row_id=rows[0]["id"])

    def upsert_suppliers(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult:
        return upsert_records(
            self.client, self._tid(T_SUPPLIERS), key_field="Supplier Key",
            fingerprint_field="Record Fingerprint",
            records=_round_records(records, _SUPPLIER_DECIMALS), index=index,
            batch_size=self.batch_size, dry_run=self.dry_run,
        )

    def upsert_products(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult:
        return upsert_records(
            self.client, self._tid(T_PRODUCTS), key_field="Product Key",
            fingerprint_field="Record Fingerprint",
            records=_round_records(records, _PRODUCT_DECIMALS), index=index,
            batch_size=self.batch_size, dry_run=self.dry_run,
        )

    def save_checkpoint(self, run: RunHandle, checkpoint: Checkpoint, counts: RunCounts) -> None:
        if self.dry_run or run.row_id is None:
            return
        self.client.batch_update(
            self._tid(T_SCAN_RUNS),
            [
                {
                    "id": run.row_id,
                    "Checkpoint Data": checkpoint.to_json(),
                    "Last Page": checkpoint.page,
                    "Last Cursor": checkpoint.cursor,
                    "Last Product Key": checkpoint.last_product_key,
                    **counts.as_baserow_fields(),
                }
            ],
        )

    def complete_run(
        self, run: RunHandle, *, status, completeness, counts, error_summary=None
    ) -> None:
        if self.dry_run or run.row_id is None:
            return
        self.client.batch_update(
            self._tid(T_SCAN_RUNS),
            [
                {
                    "id": run.row_id,
                    "Status": status.value,
                    "Completed At": now_iso(),
                    "Completeness Status": completeness.value,
                    "Error Summary": error_summary,
                    **counts.as_baserow_fields(),
                }
            ],
        )

    def load_run(self, run_id: str):
        from ..runs.checkpoints import Checkpoint

        for raw_row in self.client.iter_rows(self._tid(T_SCAN_RUNS)):
            row = _flatten(raw_row, _SCAN_RUN_NUMS)
            if row.get("Run ID") == run_id:
                cp = Checkpoint.from_json(row.get("Checkpoint Data"))
                return RunHandle(run_id=run_id, row_id=row["id"]), cp
        return None

    # --- Review ops (scoring / classification / selection) -------------------------

    def iter_suppliers(self) -> list[dict]:
        return [_flatten(r, _SUPPLIER_NUMS) for r in self.client.iter_rows(self._tid(T_SUPPLIERS))]

    def iter_products(self) -> list[dict]:
        return [_flatten(r, _PRODUCT_NUMS) for r in self.client.iter_rows(self._tid(T_PRODUCTS))]

    def update_supplier(self, row_id: int, fields: dict) -> None:
        if self.dry_run:
            return
        fields = _round_fields(fields, _SUPPLIER_DECIMALS)
        self.client.batch_update(self._tid(T_SUPPLIERS), [{"id": row_id, **fields}])

    def update_product(self, row_id: int, fields: dict) -> None:
        if self.dry_run:
            return
        fields = _round_fields(fields, _PRODUCT_DECIMALS)
        self.client.batch_update(self._tid(T_PRODUCTS), [{"id": row_id, **fields}])

    def set_product_image(self, row_id: int, content: bytes, filename: str) -> str | None:
        """Upload an image to Baserow and attach it to the product's Processed Image field."""
        if self.dry_run:
            return None
        obj = self.client.upload_file(content, filename)
        self.client.batch_update(
            self._tid(T_PRODUCTS),
            [{"id": row_id, "Processed Image": [{"name": obj["name"]}]}],
        )
        return obj.get("url")

    def update_supplier_rows(self, items: list[dict]) -> None:
        if self.dry_run or not items:
            return
        rows = [_round_fields(i, _SUPPLIER_DECIMALS) for i in items]
        batch_update_all(self.client, self._tid(T_SUPPLIERS), rows, batch_size=self.batch_size)

    def update_product_rows(self, items: list[dict]) -> None:
        if self.dry_run or not items:
            return
        rows = [_round_fields(i, _PRODUCT_DECIMALS) for i in items]
        batch_update_all(self.client, self._tid(T_PRODUCTS), rows, batch_size=self.batch_size)

    def create_selection_batch(self, fields: dict, product_row_ids: list[int]) -> int | None:
        if self.dry_run:
            return None
        rows = self.client.batch_create(
            self._tid(T_SELECTION_BATCHES), [{**fields, "Products": product_row_ids}]
        )
        return rows[0]["id"] if rows else None

    def create_manual_decision(self, fields: dict) -> int | None:
        if self.dry_run:
            return None
        rows = self.client.batch_create(self._tid(T_MANUAL_DECISIONS), [fields])
        return rows[0]["id"] if rows else None

    def create_product_change(self, fields: dict) -> int | None:
        if self.dry_run:
            return None
        rows = self.client.batch_create(self._tid(T_PRODUCT_CHANGES), [fields])
        return rows[0]["id"] if rows else None


def _flatten(row: dict, number_fields: set[str] = frozenset()) -> dict:
    """Normalize Baserow read shapes to what the app expects.

    Baserow returns single-select fields as ``{"id","value","color"}``, link fields as
    ``[{"id","value"}, ...]`` and number fields as strings. The app expects plain select
    strings, lists of row ids, and numeric numbers (matching what it writes and what
    InMemoryPersistence stores), so flatten + coerce on read.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in number_fields and isinstance(value, str):
            out[key] = _to_number(value)
        elif isinstance(value, dict) and "value" in value:
            out[key] = value["value"]  # single select -> its value string
        elif isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0]:
            out[key] = [item["id"] for item in value]  # link row -> [row_id, ...]
        else:
            out[key] = value
    return out


def _to_number(value: str):
    if value == "":
        return None
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None
