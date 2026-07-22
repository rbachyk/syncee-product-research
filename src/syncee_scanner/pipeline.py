"""Initial-assortment funnel (scan → pre-rank → enrich → re-score → select).

Ties the pieces together to produce the best ~24 candidate products from a scanned pool:

  1. scan a balanced pool (list API);
  2. score suppliers + products on list data (pre-rank);
  3. enrich the top ``enrich_top`` candidates with product detail (real margin/shipping);
  4. re-score with the enriched data + classify into collections;
  5. select the best assortment (diversity-balanced) as a candidate batch.

Nothing is auto-approved — the batch is for manual review (spec §26.6).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .enrich import EnrichResult, enrich_products
from .extraction.mapper import SynceeResponseMapper, load_mapping
from .models import RunType
from .observability.logging import get_logger
from .scan import run_scan
from .scoring.service import score_products, score_suppliers
from .selection.service import make_initial_assortment

log = get_logger(__name__)


@dataclass
class PipelineResult:
    scan_products: int
    prerank_shortlisted: int
    enrich: EnrichResult
    final_shortlisted: int
    by_collection: dict
    batch: dict


def run_initial_pipeline(
    config: AppConfig,
    persistence,
    *,
    make_source,
    make_transport,
    scan_limit: int | None = None,
    enrich_top: int = 120,
    enrich_per_supplier: int = 4,
) -> PipelineResult:
    """Run the full initial-assortment funnel (see module docstring).

    Args:
        make_source: () -> ProductSource for the scan.
        make_transport: () -> transport with ``get_detail`` for enrichment.
        scan_limit: overall product cap for the scan (per-category cap is in the mapping).
        enrich_top: how many top pre-ranked products to enrich with detail.
    """
    mapper = SynceeResponseMapper(load_mapping())

    log.info("pipeline.scan")
    scan = run_scan(config, source=make_source(), persistence=persistence,
                    run_type=RunType.FULL_SCAN, limit=scan_limit)

    # Pre-rank on list signals only. Supplier eligibility can't be known from the sparse list
    # data (no ship-to countries), so we DON'T score/gate suppliers here — leaving them
    # "Unscored" keeps products eligible so they rank on their own merit (margin/content).
    # Real supplier scoring happens after enrichment pulls the ship-to data.
    log.info("pipeline.prerank")
    pre = score_products(persistence, config)

    log.info("pipeline.enrich", top=enrich_top)
    transport = make_transport()
    try:
        enrich = enrich_products(
            persistence, transport, config, mapper,
            top=enrich_top, per_supplier_cap=enrich_per_supplier,
            max_retail=config.selection.max_retail_price,
        )
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            close()

    log.info("pipeline.rescore")
    score_suppliers(persistence, config)
    final = score_products(persistence, config)

    log.info("pipeline.select")
    batch = make_initial_assortment(persistence, config)

    return PipelineResult(
        scan_products=scan.counts.products_created + scan.counts.products_updated,
        prerank_shortlisted=pre.shortlisted,
        enrich=enrich,
        final_shortlisted=final.shortlisted,
        by_collection=final.by_collection,
        batch=batch,
    )
