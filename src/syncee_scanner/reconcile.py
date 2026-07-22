"""Reconciliation scan (spec §28).

Re-scans the accessible catalog to verify known products still exist, refresh their
price/stock/shipping via idempotent upsert, and mark products no longer seen as inactive —
without deleting any historical rows (spec §28.3). Products that reappear are reactivated by
the normal upsert path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig
from .extraction.normalization import now_iso
from .extraction.pagination import PaginationGuard
from .incremental import _normalize_page
from .models import CompletenessStatus, RunStatus, RunType, SelectionStatus
from .observability.errors import ScannerError
from .observability.logging import get_logger
from .runs.checkpoints import Checkpoint
from .runs.manager import RunCounts
from .scan import ScanSummary

log = get_logger(__name__)


@dataclass
class ReconcileResult:
    summary: ScanSummary
    inactive_marked: int = 0
    reactivated: int = 0
    missing_keys: list[str] = field(default_factory=list)


def run_reconciliation_scan(config: AppConfig, *, source, persistence) -> ReconcileResult:
    """Run a reconciliation scan (spec §28.2/§28.3)."""
    run = persistence.create_run(
        run_type=RunType.RECONCILIATION,
        category=config.syncee.category,
        config_hash=config.config_hash(),
        scanner_version=config.scanner_version,
    )
    log.info("reconcile.started", run_id=run.run_id)

    supplier_index = persistence.load_supplier_index()
    product_index = persistence.load_product_index()
    known_before = product_index.keys()
    counts = RunCounts()
    guard = PaginationGuard(max_pages=config.safety.max_pages)
    seen_keys: set[str] = set()
    seen_suppliers: set[str] = set()
    error: str | None = None

    try:
        for page in source.iter_pages():
            guard.check(page_number=page.page_number, cursor=page.cursor)
            supplier_records, product_pairs = _normalize_page(page, run)
            seen_suppliers.update(r.key for r in supplier_records)
            sup_result = persistence.upsert_suppliers(supplier_records, supplier_index)
            counts.add_supplier_result(
                sup_result.created, sup_result.updated, sup_result.unchanged
            )

            from .baserow.mapping import product_upsert_record

            records = []
            for norm_product, raw, skey in product_pairs:
                supplier_row_id = (
                    sup_result.key_to_row_id.get(skey) or supplier_index.row_id(skey)
                )
                if supplier_row_id is None:
                    continue
                seen_keys.add(norm_product["product_key"])
                records.append(
                    product_upsert_record(
                        norm_product, now=now_iso(), supplier_row_id=supplier_row_id,
                        scan_run_row_id=run.row_id, raw=raw,
                    )
                )
            prod_result = persistence.upsert_products(records, product_index)
            counts.add_product_result(
                prod_result.created, prod_result.updated, prod_result.unchanged
            )
            counts.pages_processed += 1

        status = RunStatus.COMPLETED
    except ScannerError as exc:
        status = RunStatus.FAILED
        error = exc.to_dict()["message"]
        log.error("reconcile.failed", run_id=run.run_id, error_code=exc.code.value)

    result = ReconcileResult(summary=None)  # type: ignore[arg-type]

    # Mark known products that were not seen this run as inactive (spec §28.3).
    if status == RunStatus.COMPLETED:
        missing = known_before - seen_keys
        for key in missing:
            row_id = product_index.row_id(key)
            if row_id is not None:
                # Keep Last Seen At (records when it was genuinely last seen); no deletes.
                # Invalidate the stored fingerprint so that if the product later
                # reappears the upsert detects a change and reactivates it (Active=True).
                persistence.update_product(
                    row_id,
                    {
                        "Active": False,
                        "Selection Status": SelectionStatus.ARCHIVED.value,
                        "Record Fingerprint": "archived",
                    },
                )
        result.inactive_marked = len(missing)
        result.missing_keys = sorted(missing)

    completeness = (
        CompletenessStatus.COMPLETE if status == RunStatus.COMPLETED
        else CompletenessStatus.UNVERIFIED
    )
    persistence.complete_run(
        run, status=status, completeness=completeness, counts=counts, error_summary=error
    )
    persistence.save_checkpoint(run, Checkpoint(updated_at=now_iso()), counts)

    result.summary = ScanSummary(
        run_id=run.run_id, run_type=RunType.RECONCILIATION.value, status=status.value,
        completeness=completeness.value, counts=counts, error=error,
        supplier_count=len(seen_suppliers),
    )
    log.info("reconcile.completed", run_id=run.run_id, inactive=result.inactive_marked,
             completeness=completeness.value)
    return result
