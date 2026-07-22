"""Scan orchestration (spec §17).

Drives a :class:`~.extraction.source.ProductSource` through pages, normalizes and
deduplicates records, upserts suppliers *before* their products (so product→supplier links
resolve, spec §17.3), checkpoints after each page (spec §17.3 step 13), and writes a run
summary. Persistence is injected via the :class:`~.runs.persistence.ScanPersistence`
protocol so the same orchestration runs against Baserow or fully in memory.

This is the Phase-2 "limited scanner"; ``limit`` caps products for smoke tests
(``scan full --limit 50 --dry-run``, spec §41.3). Scoring/classification/selection are
later phases and are not invoked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .baserow.indexes import KeyIndex
from .baserow.mapping import product_upsert_record, supplier_upsert_record
from .config import AppConfig
from .extraction.normalization import now_iso
from .extraction.pagination import PaginationGuard
from .extraction.records import normalize_product, normalize_supplier
from .extraction.source import ProductSource
from .models import CompletenessStatus, RunStatus, RunType
from .observability.errors import ErrorCode, ScannerError
from .observability.logging import get_logger
from .runs.checkpoints import Checkpoint
from .runs.manager import RunCounts, RunHandle
from .runs.persistence import ScanPersistence

log = get_logger(__name__)


@dataclass
class ScanSummary:
    run_id: str
    run_type: str
    status: str
    completeness: str
    counts: RunCounts
    error: str | None = None
    supplier_count: int = 0

    def as_console_dict(self) -> dict:
        """Concise console summary (spec §35)."""
        c = self.counts
        return {
            "run_id": self.run_id,
            "run_type": self.run_type,
            "status": self.status,
            "pages": c.pages_processed,
            "products_seen": c.products_seen,
            "new_products": c.new_products,
            "changed_products": c.products_updated,
            "failed_products": c.products_failed,
            "suppliers_discovered": self.supplier_count,
            "completeness": self.completeness,
        }


@dataclass
class _PageBatch:
    supplier_records: list = field(default_factory=list)
    product_pairs: list = field(default_factory=list)  # (normalized_product, raw, supplier_key)
    relevant_counts: dict = field(default_factory=dict)  # supplier_key -> count


def run_scan(
    config: AppConfig,
    *,
    source: ProductSource,
    persistence: ScanPersistence,
    run_type: RunType = RunType.FULL_SCAN,
    limit: int | None = None,
    start_cursor: str | None = None,
    resume_run: RunHandle | None = None,
) -> ScanSummary:
    """Run a (possibly limited) catalog scan end to end (spec §17.3)."""
    run = resume_run or persistence.create_run(
        run_type=run_type,
        category=config.syncee.category,
        config_hash=config.config_hash(),
        scanner_version=config.scanner_version,
    )
    log.info("scan.started", run_id=run.run_id, run_type=run_type.value, dry_limit=limit)

    supplier_index = persistence.load_supplier_index()
    product_index = persistence.load_product_index()
    counts = RunCounts()
    guard = PaginationGuard(max_pages=config.safety.max_pages)
    checkpoint = Checkpoint(cursor=start_cursor)
    seen_suppliers: set[str] = set()
    completeness = CompletenessStatus.COMPLETE
    error: str | None = None
    products_remaining = limit

    try:
        for page in source.iter_pages(start_cursor=start_cursor):
            guard.check(page_number=page.page_number, cursor=page.cursor)
            subcategory = _page_subcategory(page, config)
            batch, last_key = _normalize_page(
                page, products_remaining, counts, run, subcategory,
                config.markets.target_codes,
            )

            # Suppliers first so links resolve (spec §17.3 steps 7-8).
            sup_result = persistence.upsert_suppliers(batch.supplier_records, supplier_index)
            counts.add_supplier_result(
                sup_result.created, sup_result.updated, sup_result.unchanged
            )
            seen_suppliers.update(rec.key for rec in batch.supplier_records)

            product_records = _build_product_records(batch, sup_result, supplier_index, run)
            prod_result = persistence.upsert_products(product_records, product_index)
            counts.add_product_result(
                prod_result.created, prod_result.updated, prod_result.unchanged
            )

            counts.pages_processed += 1
            checkpoint = Checkpoint(
                page=page.page_number,
                cursor=page.cursor,
                last_product_key=last_key,
                products_processed=counts.products_seen,
                suppliers_processed=len(seen_suppliers),
                updated_at=now_iso(),
            )
            persistence.save_checkpoint(run, checkpoint, counts)
            log.info(
                "scan.page_processed", run_id=run.run_id, page=page.page_number,
                products=len(product_records), new=prod_result.created,
                changed=prod_result.updated,
            )

            if products_remaining is not None:
                products_remaining -= len(batch.product_pairs)
                if products_remaining <= 0:
                    completeness = CompletenessStatus.PARTIAL
                    log.info("scan.limit_reached", run_id=run.run_id, limit=limit)
                    break

        status = RunStatus.COMPLETED
    except ScannerError as exc:
        status = RunStatus.FAILED
        completeness = CompletenessStatus.UNVERIFIED
        error = exc.to_dict()["message"]
        log.error("scan.failed", run_id=run.run_id, error_code=exc.code.value, message=error)

    persistence.complete_run(
        run, status=status, completeness=completeness, counts=counts, error_summary=error
    )
    summary = ScanSummary(
        run_id=run.run_id, run_type=run_type.value, status=status.value,
        completeness=completeness.value, counts=counts, error=error,
        supplier_count=len(seen_suppliers),
    )
    log.info("scan.completed", **summary.as_console_dict())
    return summary


def resume_scan(
    config: AppConfig, *, source: ProductSource, persistence: ScanPersistence, run_id: str
) -> ScanSummary:
    """Resume an interrupted scan from its stored checkpoint (spec §17.5)."""
    loaded = persistence.load_run(run_id)
    if loaded is None:
        raise ScannerError(ErrorCode.CHECKPOINT_ERROR, f"Run '{run_id}' not found")
    run, checkpoint = loaded
    log.info("scan.resume", run_id=run_id, from_cursor=checkpoint.cursor, page=checkpoint.page)
    return run_scan(
        config, source=source, persistence=persistence,
        run_type=RunType.FULL_SCAN, start_cursor=checkpoint.cursor, resume_run=run,
    )


def _page_subcategory(page, config: AppConfig) -> str | None:
    """Human label for the subcategory this page was scanned under (offset mode)."""
    cat = page.meta.get("category") if page.meta else None
    if cat is None:
        return None
    return config.classification.category_labels.get(str(cat))


def _normalize_page(
    page, products_remaining, counts, run: RunHandle, subcategory: str | None = None,
    target_codes: list[str] | None = None,
) -> tuple[_PageBatch, str | None]:
    """Normalize a page's products + their embedded suppliers (spec §18)."""
    batch = _PageBatch()
    supplier_by_key: dict[str, dict] = {}
    last_key: str | None = None
    taken = 0

    for raw in page.products:
        if products_remaining is not None and taken >= products_remaining:
            break
        try:
            raw_supplier = raw.get("supplier") or {}
            norm_supplier = normalize_supplier(raw_supplier)
            skey = norm_supplier["supplier_key"]
            supplier_by_key[skey] = (norm_supplier, raw_supplier)

            norm_product = normalize_product(
                raw, supplier_key_value=skey, target_codes=target_codes
            )
            # Stamp the scanned subcategory so classification can map it reliably.
            if subcategory and not norm_product.get("syncee_subcategory"):
                norm_product["syncee_subcategory"] = subcategory
            batch.product_pairs.append((norm_product, raw, skey))
            batch.relevant_counts[skey] = batch.relevant_counts.get(skey, 0) + 1
            last_key = norm_product["product_key"]
            taken += 1
        except (ValueError, KeyError) as exc:
            counts.products_failed += 1
            log.warning("scan.product_parse_failed", error=str(exc))

    now = now_iso()
    for skey, (norm_supplier, raw_supplier) in supplier_by_key.items():
        batch.supplier_records.append(
            supplier_upsert_record(
                norm_supplier, now=now,
                relevant_product_count=batch.relevant_counts.get(skey, 0),
                scan_run_row_id=run.row_id, raw=raw_supplier,
            )
        )
    return batch, last_key


def _build_product_records(batch, sup_result, supplier_index: KeyIndex, run: RunHandle):
    """Attach resolved supplier row IDs and build product UpsertRecords."""
    now = now_iso()
    records = []
    for norm_product, raw, skey in batch.product_pairs:
        supplier_row_id = sup_result.key_to_row_id.get(skey) or supplier_index.row_id(skey)
        if supplier_row_id is None:
            continue
        records.append(
            product_upsert_record(
                norm_product, now=now, supplier_row_id=supplier_row_id,
                scan_run_row_id=run.row_id, raw=raw,
            )
        )
    return records
