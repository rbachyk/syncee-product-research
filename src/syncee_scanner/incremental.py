"""Incremental weekly scan (spec §27) with Product Changes recording (spec §13).

Implements Strategy B (newest-first scanning, spec §27.4): walk the source from newest,
upsert suppliers-then-products idempotently, and stop after a configurable number of
consecutive already-known products/pages. New products are flagged ``Is New``; changed
products produce a Product Changes row (spec §13). Completeness is reported honestly: if
newest-first ordering can't be verified the run is marked ``Unverified`` (spec §27.5).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .baserow.mapping import product_upsert_record, supplier_upsert_record
from .changes.detector import detect_product_change
from .config import AppConfig
from .extraction.normalization import now_iso
from .extraction.pagination import PaginationGuard
from .extraction.records import normalize_product, normalize_supplier
from .models import CompletenessStatus, RunStatus, RunType
from .observability.errors import ScannerError
from .observability.logging import get_logger
from .runs.checkpoints import Checkpoint
from .runs.manager import RunCounts
from .scan import ScanSummary

log = get_logger(__name__)


@dataclass
class _Prev:
    """Previously-stored product state used to diff changes."""

    normalized: dict
    row_id: int


def _load_previous_products(persistence) -> dict[str, _Prev]:
    """Reconstruct previous normalized products from stored Raw Data, keyed by Product Key."""
    prev: dict[str, _Prev] = {}
    for row in persistence.iter_products():
        key = row.get("Product Key")
        raw = row.get("Raw Data")
        if not key or not raw:
            continue
        try:
            raw_obj = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        supplier_key = ""
        if raw_obj.get("supplier"):
            supplier_key = normalize_supplier(raw_obj["supplier"]).get("supplier_key", "")
        prev[key] = _Prev(
            normalized=normalize_product(raw_obj, supplier_key_value=supplier_key),
            row_id=row["id"],
        )
    return prev


def _change_id() -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    return f"change-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class IncrementalResult:
    summary: ScanSummary
    changes_recorded: int = 0
    stopped_early: bool = False
    new_product_keys: list[str] = field(default_factory=list)


def run_incremental_scan(
    config: AppConfig,
    *,
    source,
    persistence,
    newest_first_verified: bool = False,
) -> IncrementalResult:
    """Run an incremental newest-first scan (spec §27.4 Strategy B)."""
    run = persistence.create_run(
        run_type=RunType.INCREMENTAL_SCAN,
        category=config.syncee.category,
        config_hash=config.config_hash(),
        scanner_version=config.scanner_version,
    )
    log.info("incremental.started", run_id=run.run_id, newest_first=newest_first_verified)

    supplier_index = persistence.load_supplier_index()
    product_index = persistence.load_product_index()
    previous = _load_previous_products(persistence)
    counts = RunCounts()
    guard = PaginationGuard(max_pages=config.safety.max_pages)
    inc = config.incremental_scan
    result = IncrementalResult(summary=None)  # type: ignore[arg-type]

    consecutive_known = 0
    known_pages = 0
    stopped_early = False
    error: str | None = None
    seen_supplier_keys: set[str] = set()

    try:
        for page in source.iter_pages():
            guard.check(page_number=page.page_number, cursor=page.cursor)
            page_all_known = True

            supplier_records, product_pairs = _normalize_page(page, run)
            seen_supplier_keys.update(r.key for r in supplier_records)
            sup_result = persistence.upsert_suppliers(supplier_records, supplier_index)
            counts.add_supplier_result(
                sup_result.created, sup_result.updated, sup_result.unchanged
            )

            product_records = []
            for norm_product, raw, skey in product_pairs:
                key = norm_product["product_key"]
                supplier_row_id = sup_result.key_to_row_id.get(skey) or supplier_index.row_id(skey)
                if supplier_row_id is None:
                    continue
                if key in product_index:
                    consecutive_known += 1
                else:
                    consecutive_known = 0
                    page_all_known = False
                    result.new_product_keys.append(key)
                product_records.append(
                    (product_upsert_record(
                        norm_product, now=now_iso(), supplier_row_id=supplier_row_id,
                        scan_run_row_id=run.row_id, raw=raw,
                    ), norm_product)
                )

            prod_result = persistence.upsert_products(
                [r for r, _ in product_records], product_index
            )
            counts.add_product_result(
                prod_result.created, prod_result.updated, prod_result.unchanged
            )

            # Record Product Changes for changed products (spec §13, §19.3).
            result.changes_recorded += _record_changes(
                persistence, run, prod_result, product_records, previous
            )

            counts.pages_processed += 1
            known_pages = known_pages + 1 if page_all_known else 0

            if (
                consecutive_known >= inc.stop_after_known_products
                or known_pages >= inc.stop_after_known_pages
            ):
                stopped_early = True
                log.info(
                    "incremental.stopped_early", run_id=run.run_id,
                    consecutive_known=consecutive_known, known_pages=known_pages,
                )
                break

        status = RunStatus.COMPLETED
    except ScannerError as exc:
        status = RunStatus.FAILED
        error = exc.to_dict()["message"]
        log.error("incremental.failed", run_id=run.run_id, error_code=exc.code.value)

    completeness = _completeness(newest_first_verified, stopped_early, status)
    persistence.complete_run(
        run, status=status, completeness=completeness, counts=counts, error_summary=error
    )
    persistence.save_checkpoint(run, Checkpoint(updated_at=now_iso()), counts)

    result.stopped_early = stopped_early
    result.summary = ScanSummary(
        run_id=run.run_id, run_type=RunType.INCREMENTAL_SCAN.value, status=status.value,
        completeness=completeness.value, counts=counts, error=error,
        supplier_count=len(seen_supplier_keys),
    )
    log.info("incremental.completed", run_id=run.run_id, new=len(result.new_product_keys),
             changes=result.changes_recorded, completeness=completeness.value)
    return result


def _normalize_page(page, run):
    supplier_by_key: dict[str, tuple[dict, dict]] = {}
    product_pairs = []
    relevant: dict[str, int] = {}
    for raw in page.products:
        raw_supplier = raw.get("supplier") or {}
        norm_supplier = normalize_supplier(raw_supplier)
        skey = norm_supplier["supplier_key"]
        supplier_by_key[skey] = (norm_supplier, raw_supplier)
        norm_product = normalize_product(raw, supplier_key_value=skey)
        product_pairs.append((norm_product, raw, skey))
        relevant[skey] = relevant.get(skey, 0) + 1

    now = now_iso()
    supplier_records = [
        supplier_upsert_record(
            ns, now=now, relevant_product_count=relevant.get(sk, 0),
            scan_run_row_id=run.row_id, raw=rs,
        )
        for sk, (ns, rs) in supplier_by_key.items()
    ]
    return supplier_records, product_pairs


def _record_changes(persistence, run, prod_result, product_records, previous) -> int:
    changed = set(prod_result.changed_keys)
    if not changed:
        return 0
    norm_by_key = {np["product_key"]: np for _, np in product_records}
    recorded = 0
    for key in changed:
        prev = previous.get(key)
        current = norm_by_key.get(key)
        if not prev or current is None:
            continue
        change = detect_product_change(prev.normalized, current)
        if not change.changed:
            continue
        persistence.create_product_change(
            {
                "Change ID": _change_id(),
                "Product": [prev.row_id],
                "Scan Run": [run.row_id] if run.row_id else [],
                "Detected At": now_iso(),
                "Changed Fields": ", ".join(change.changed_fields),
                "Previous Values": json.dumps(change.previous_values, default=str),
                "New Values": json.dumps(change.new_values, default=str),
                "Change Type": change.change_type.value if change.change_type else "",
            }
        )
        recorded += 1
    return recorded


def _completeness(newest_first_verified, stopped_early, status) -> CompletenessStatus:
    if status == RunStatus.FAILED:
        return CompletenessStatus.UNVERIFIED
    if not newest_first_verified:
        return CompletenessStatus.UNVERIFIED
    if stopped_early:
        return CompletenessStatus.COMPLETE_WITH_KNOWN_LIMITATIONS
    return CompletenessStatus.COMPLETE
