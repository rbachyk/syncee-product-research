"""Persistence facade for scans (spec §16.4).

The scan orchestrator depends on this narrow :class:`ScanPersistence` protocol rather than
on Baserow directly, so it can be driven offline in tests via :class:`InMemoryPersistence`
and against live Baserow via ``baserow.persistence.BaserowPersistence``.
"""

from __future__ import annotations

from typing import Protocol

from ..baserow.indexes import KeyIndex
from ..baserow.repositories import UpsertRecord, UpsertResult
from ..models import CompletenessStatus, RunStatus, RunType
from .checkpoints import Checkpoint
from .manager import RunCounts, RunHandle, new_run_id


class ScanPersistence(Protocol):
    def load_supplier_index(self) -> KeyIndex: ...
    def load_product_index(self) -> KeyIndex: ...
    def create_run(
        self, *, run_type: RunType, category: str, config_hash: str, scanner_version: str
    ) -> RunHandle: ...
    def upsert_suppliers(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult: ...
    def upsert_products(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult: ...
    def save_checkpoint(
        self, run: RunHandle, checkpoint: Checkpoint, counts: RunCounts
    ) -> None: ...
    def load_run(self, run_id: str) -> tuple[RunHandle, Checkpoint] | None: ...
    def complete_run(
        self,
        run: RunHandle,
        *,
        status: RunStatus,
        completeness: CompletenessStatus,
        counts: RunCounts,
        error_summary: str | None = None,
    ) -> None: ...


class ReviewOps(Protocol):
    """Read/update operations used by scoring, classification and selection."""

    def iter_suppliers(self) -> list[dict]: ...
    def iter_products(self) -> list[dict]: ...
    def update_supplier(self, row_id: int, fields: dict) -> None: ...
    def update_product(self, row_id: int, fields: dict) -> None: ...
    def update_supplier_rows(self, items: list[dict]) -> None: ...
    def update_product_rows(self, items: list[dict]) -> None: ...
    def set_product_image(self, row_id: int, content: bytes, filename: str) -> str | None: ...
    def create_selection_batch(self, fields: dict, product_row_ids: list[int]) -> int: ...
    def create_manual_decision(self, fields: dict) -> int: ...
    def create_product_change(self, fields: dict) -> int: ...


class InMemoryPersistence:
    """A fully in-memory persistence backend for offline scans and tests.

    Mimics idempotent upsert semantics (spec §16.5) using the same UpsertRecord logic the
    Baserow backend uses, without any HTTP.
    """

    def __init__(self) -> None:
        self.suppliers: dict[str, dict] = {}
        self.products: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.checkpoints: dict[str, Checkpoint] = {}
        self.selection_batches: dict[int, dict] = {}
        self.manual_decisions: list[dict] = []
        self.product_changes: list[dict] = []
        self._next_row_id = 1

    def load_supplier_index(self) -> KeyIndex:
        return self._rebuild_index(self.suppliers)

    def load_product_index(self) -> KeyIndex:
        return self._rebuild_index(self.products)

    @staticmethod
    def _rebuild_index(store: dict[str, dict]) -> KeyIndex:
        # Rebuild from stored rows so the row's Record Fingerprint is the source of
        # truth (matching the Baserow backend), not a long-lived in-memory object.
        index = KeyIndex()
        for key, row in store.items():
            index.add(key, row["id"], row.get("Record Fingerprint"))
        return index

    def create_run(self, *, run_type, category, config_hash, scanner_version) -> RunHandle:
        run_id = new_run_id(run_type)
        row_id = self._alloc()
        self.runs[run_id] = {
            "row_id": row_id,
            "Run Type": run_type.value,
            "Status": RunStatus.RUNNING.value,
            "Category": category,
            "Configuration Hash": config_hash,
            "Scanner Version": scanner_version,
        }
        return RunHandle(run_id=run_id, row_id=row_id)

    def _upsert(self, records, index, store) -> UpsertResult:
        result = UpsertResult()
        for rec in records:
            entry = index.get(rec.key)
            if entry is None:
                row_id = self._alloc()
                store[rec.key] = {**rec.fields, **rec.create_extra, "id": row_id,
                                  "Record Fingerprint": rec.fingerprint}
                index.add(rec.key, row_id, rec.fingerprint)
                result.created += 1
                result.key_to_row_id[rec.key] = row_id
            elif entry.fingerprint != rec.fingerprint:
                store[rec.key].update({**rec.fields, **rec.changed_extra,
                                       "Record Fingerprint": rec.fingerprint})
                index.add(rec.key, entry.row_id, rec.fingerprint)
                result.updated += 1
                result.changed_keys.append(rec.key)
                result.key_to_row_id[rec.key] = entry.row_id
            else:
                if rec.touch_fields:
                    store[rec.key].update(rec.touch_fields)
                result.unchanged += 1
                result.key_to_row_id[rec.key] = entry.row_id
        return result

    def upsert_suppliers(self, records, index) -> UpsertResult:
        return self._upsert(records, index, self.suppliers)

    def upsert_products(self, records, index) -> UpsertResult:
        return self._upsert(records, index, self.products)

    def save_checkpoint(self, run, checkpoint, counts) -> None:
        self.checkpoints[run.run_id] = checkpoint

    def load_run(self, run_id: str) -> tuple[RunHandle, Checkpoint] | None:
        run = self.runs.get(run_id)
        if not run:
            return None
        cp = self.checkpoints.get(run_id, Checkpoint())
        return RunHandle(run_id=run_id, row_id=run.get("row_id")), cp

    def complete_run(self, run, *, status, completeness, counts, error_summary=None) -> None:
        self.runs.setdefault(run.run_id, {"row_id": run.row_id}).update(
            {
                "Status": status.value,
                "Completeness Status": completeness.value,
                "Error Summary": error_summary,
                **counts.as_baserow_fields(),
            }
        )

    # --- Review ops (scoring / classification / selection) -------------------------

    def iter_suppliers(self) -> list[dict]:
        return list(self.suppliers.values())

    def iter_products(self) -> list[dict]:
        return list(self.products.values())

    def update_supplier(self, row_id: int, fields: dict) -> None:
        self._update_by_id(self.suppliers, row_id, fields)

    def update_product(self, row_id: int, fields: dict) -> None:
        self._update_by_id(self.products, row_id, fields)

    def set_product_image(self, row_id: int, content: bytes, filename: str) -> str | None:
        # Offline backend: record a marker instead of hosting the bytes.
        marker = f"inmemory://{filename}"
        self._update_by_id(self.products, row_id, {"Processed Image": [{"name": filename}]})
        return marker

    def update_supplier_rows(self, items: list[dict]) -> None:
        for item in items:
            self._update_by_id(self.suppliers, item["id"], {k: v for k, v in item.items()
                                                            if k != "id"})

    def update_product_rows(self, items: list[dict]) -> None:
        for item in items:
            self._update_by_id(self.products, item["id"], {k: v for k, v in item.items()
                                                          if k != "id"})

    def create_selection_batch(self, fields: dict, product_row_ids: list[int]) -> int:
        row_id = self._alloc()
        self.selection_batches[row_id] = {**fields, "id": row_id, "Products": product_row_ids}
        return row_id

    def create_manual_decision(self, fields: dict) -> int:
        row_id = self._alloc()
        self.manual_decisions.append({**fields, "id": row_id})
        return row_id

    def create_product_change(self, fields: dict) -> int:
        row_id = self._alloc()
        self.product_changes.append({**fields, "id": row_id})
        return row_id

    @staticmethod
    def _update_by_id(store: dict[str, dict], row_id: int, fields: dict) -> None:
        for row in store.values():
            if row.get("id") == row_id:
                row.update(fields)
                return

    def _alloc(self) -> int:
        rid = self._next_row_id
        self._next_row_id += 1
        return rid
