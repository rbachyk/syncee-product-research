"""Product-detail enrichment (spec §5.4 — Stage 2 of the selection funnel).

Fetches Syncee's product-detail API for the top-ranked candidates and updates their Baserow
rows with the real description, shipping cost/time, stock, category, brand and supplier
ship-to/contact data that the list API omits. Re-scoring after enrichment then uses real
margin and shipping instead of estimates.

Detail is fetched only for a bounded set of finalists (ranked by the list-based pre-score),
keeping the number of per-product calls small.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .baserow.mapping import product_upsert_record
from .config import AppConfig
from .extraction.mapper import SynceeResponseMapper
from .extraction.normalization import now_iso
from .extraction.records import normalize_product, normalize_supplier
from .observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class EnrichResult:
    enriched: int = 0
    failed: int = 0
    suppliers_updated: int = 0
    enriched_keys: list[str] = field(default_factory=list)


def _supplier_enrich_fields(ns: dict, raw_supplier: dict) -> dict:
    """Supplier fields the detail response improves, incl. refreshed Raw Data.

    Raw Data is refreshed with the enriched canonical supplier so a later supplier re-score
    re-normalizes the rich ship-to / shipping data (otherwise it re-reads the sparse list
    supplier and the target-market gate fails). Aggregate fields (Relevant Product Count,
    First Seen At) are read from the row, not Raw Data, so they're preserved.
    """
    fields: dict = {"Raw Data": json.dumps(raw_supplier, ensure_ascii=False, default=str)}
    if ns.get("ships_to_countries"):
        fields["Ships To Countries"] = ", ".join(ns["ships_to_countries"])
    if ns.get("location_country"):
        fields["Location Country"] = ns["location_country"]
    if ns.get("supplier_url"):
        fields["Supplier URL"] = ns["supplier_url"]
    if ns.get("shipping_min_days") is not None:
        fields["Shipping Min Days"] = ns["shipping_min_days"]
    if ns.get("shipping_max_days") is not None:
        fields["Shipping Max Days"] = ns["shipping_max_days"]
    if ns.get("contact_information_available") is not None:
        fields["Contact Information Available"] = ns["contact_information_available"]
    return fields


def _select_targets(
    persistence, *, product_keys, top, review_status, per_supplier_cap, max_retail=None,
    skip_enriched=False, collection=None, limit=None,
) -> list[dict]:
    rows = list(persistence.iter_products())
    if product_keys is not None:
        keyset = set(product_keys)
        rows = [r for r in rows if r.get("Product Key") in keyset]
    if skip_enriched:
        # Resumable chunking: leave out anything already enriched.
        rows = [r for r in rows if not r.get("Enriched At")]
    if collection is not None:
        rows = [r for r in rows if r.get("Collection") == collection]
    if review_status is not None:
        rows = [r for r in rows if r.get("Review Status") in review_status]
    if max_retail is not None:
        # Focus enrichment on products whose (pre-rank) retail is within the price band.
        rows = [r for r in rows if (r.get("Proposed Retail Price") or 0) <= max_retail]
    # Rank by the list-based pre-score, best first.
    rows.sort(key=lambda r: (r.get("Product Score") or 0.0), reverse=True)
    if not per_supplier_cap:
        selected = rows[:top] if top else rows
        return selected[:limit] if limit else selected
    # Spread enrichment across suppliers so more suppliers get real data (better diversity):
    # take best products but at most `per_supplier_cap` per supplier.
    selected: list[dict] = []
    per_supplier: dict = {}
    for r in rows:
        sup = (r.get("Supplier") or [None])[0]
        if per_supplier.get(sup, 0) >= per_supplier_cap:
            continue
        selected.append(r)
        per_supplier[sup] = per_supplier.get(sup, 0) + 1
        if top and len(selected) >= top:
            break
        if limit and len(selected) >= limit:
            break
    return selected


def enrich_products(
    persistence,
    transport,
    config: AppConfig,
    mapper: SynceeResponseMapper,
    *,
    product_keys: list[str] | None = None,
    top: int | None = None,
    review_status: set[str] | None = None,
    per_supplier_cap: int = 0,
    max_retail: float | None = None,
    skip_enriched: bool = False,
    collection: str | None = None,
    limit: int | None = None,
) -> EnrichResult:
    """Enrich products with detail data (spec §5.4). Returns a summary.

    Args:
        product_keys: enrich exactly these; else use ``top``/``review_status`` filters.
        top: enrich only the highest-pre-scored N products (``None`` = no cap → all).
        review_status: restrict to products in these review statuses (e.g. shortlisted).
        per_supplier_cap: at most this many products per supplier (0 = unlimited), to spread
            enrichment across more suppliers.
        skip_enriched: leave out products that already have ``Enriched At`` (resumable chunking).
        collection: restrict to one collection.
        limit: cap the number of products enriched this run (a chunk).
    """
    targets = _select_targets(
        persistence, product_keys=product_keys, top=top, review_status=review_status,
        per_supplier_cap=per_supplier_cap, max_retail=max_retail,
        skip_enriched=skip_enriched, collection=collection, limit=limit,
    )
    log.info("enrich.started", targets=len(targets))
    result = EnrichResult()
    seen_suppliers: set[int] = set()
    product_updates: list[dict] = []
    supplier_updates: list[dict] = []

    for row in targets:
        pid = row.get("Syncee Product ID")
        if not pid:
            continue
        try:
            detail = transport.get_detail(pid)
        except Exception as exc:  # network/transport error — skip, keep going
            log.warning("enrich.fetch_failed", product_id=pid, error=str(exc)[:120])
            result.failed += 1
            continue
        if not detail:
            result.failed += 1
            continue

        raw = mapper.map_product(detail)
        ns = normalize_supplier(raw["supplier"])
        norm = normalize_product(
            raw, supplier_key_value=ns["supplier_key"],
            target_codes=config.markets.target_codes,
        )

        supplier_link = row.get("Supplier") or []
        supplier_row_id = supplier_link[0] if supplier_link else None

        # Store the canonical mapped record as Raw Data (like the scan does) so a later
        # re-score can re-normalize it — not the raw Syncee detail JSON.
        rec = product_upsert_record(
            norm, now=now_iso(), supplier_row_id=supplier_row_id or 0,
            scan_run_row_id=None, raw=raw,
        )
        fields = {**rec.fields, **rec.changed_extra, "Record Fingerprint": rec.fingerprint,
                  "Enriched At": now_iso()}
        if supplier_row_id is None:
            fields.pop("Supplier", None)
        product_updates.append({"id": row["id"], **fields})
        result.enriched += 1
        result.enriched_keys.append(row.get("Product Key"))

        if supplier_row_id and supplier_row_id not in seen_suppliers:
            supplier_updates.append(
                {"id": supplier_row_id, **_supplier_enrich_fields(ns, raw["supplier"])}
            )
            seen_suppliers.add(supplier_row_id)
            result.suppliers_updated += 1

    persistence.update_product_rows(product_updates)
    persistence.update_supplier_rows(supplier_updates)

    log.info("enrich.completed", enriched=result.enriched, failed=result.failed,
             suppliers=result.suppliers_updated)
    return result
