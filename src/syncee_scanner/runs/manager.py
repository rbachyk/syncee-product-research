"""Run identity and aggregate counters (spec §12).

Small value types shared by the scan orchestrator and the persistence backends: a run
handle (Baserow row id + human run id) and the running tallies written to the Scan Runs
row (spec §12.2, §35 console summary).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..models import RunType


@dataclass
class RunHandle:
    run_id: str
    row_id: int | None = None


@dataclass
class RunCounts:
    """Aggregate counters for a run (spec §12.2)."""

    products_seen: int = 0
    products_created: int = 0
    products_updated: int = 0
    products_unchanged: int = 0
    products_failed: int = 0
    suppliers_created: int = 0
    suppliers_updated: int = 0
    suppliers_unchanged: int = 0
    pages_processed: int = 0
    new_products: int = 0

    def add_supplier_result(self, created: int, updated: int, unchanged: int) -> None:
        self.suppliers_created += created
        self.suppliers_updated += updated
        self.suppliers_unchanged += unchanged

    def add_product_result(self, created: int, updated: int, unchanged: int) -> None:
        self.products_created += created
        self.products_updated += updated
        self.products_unchanged += unchanged
        self.products_seen += created + updated + unchanged
        self.new_products += created

    def as_baserow_fields(self) -> dict:
        return {
            "Products Seen": self.products_seen,
            "Products Created": self.products_created,
            "Products Updated": self.products_updated,
            "Products Unchanged": self.products_unchanged,
            "Products Failed": self.products_failed,
            "Suppliers Created": self.suppliers_created,
            "Suppliers Updated": self.suppliers_updated,
            "Suppliers Unchanged": self.suppliers_unchanged,
            "Pages Processed": self.pages_processed,
        }


def new_run_id(run_type: RunType) -> str:
    """Generate a sortable, unique run id, e.g. ``full-scan-20260719T172400-ab12cd``."""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    slug = run_type.value.lower().replace(" ", "-")
    return f"{slug}-{stamp}-{uuid.uuid4().hex[:6]}"
