"""Selection orchestration (spec §26, §29).

Reads shortlisted products from a :class:`~..runs.persistence.ReviewOps` backend, builds
candidates, runs the deterministic selector, creates a Selection Batch row and marks the
chosen products as candidates. No product is auto-selected/published — manual approval is
still required (spec §26.6, §29.6).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from ..config import AppConfig
from ..models import (
    BatchStatus,
    BatchType,
    Collection,
    DecisionValue,
    EntityType,
    ProductReviewStatus,
    SelectionStatus,
)
from .diversity import (
    Candidate,
    SelectionState,
    max_products_per_supplier,
    selection_score,
    violates_hard_constraints,
)
from .initial import SelectionResult, select_initial
from .new_arrivals import select_new_arrivals


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _content_potential(row: dict) -> float:
    raw = row.get("Raw Data")
    images = 0
    if isinstance(raw, str) and raw:
        try:
            images = len(json.loads(raw).get("images") or [])
        except json.JSONDecodeError:
            images = 0
    return min(1.0, 0.4 + 0.15 * images)


def build_candidates(
    persistence, *, only_new: bool = False, config: AppConfig | None = None
) -> list[Candidate]:
    """Build selection candidates from shortlisted product rows (within the price band)."""
    max_retail = config.selection.max_retail_price if config else None
    min_retail = config.selection.min_retail_price if config else None
    supplier_key_by_id = {
        row["id"]: row.get("Supplier Key") for row in persistence.iter_suppliers()
    }
    # Selectable: auto-shortlisted, manually approved, or held for manual review (the latter
    # covers fragile tableware/mirrors that the risk gate routes to review — legitimate here,
    # and nothing publishes without the manual Baserow gate anyway).
    selectable = {
        ProductReviewStatus.SHORTLISTED.value,
        ProductReviewStatus.APPROVED.value,
        ProductReviewStatus.MANUAL_REVIEW.value,
    }
    candidates: list[Candidate] = []
    for row in persistence.iter_products():
        if row.get("Review Status") not in selectable:
            continue
        if row.get("Selection Status") not in (None, SelectionStatus.NOT_SELECTED.value):
            continue  # already selected/published
        if only_new and not row.get("Is New", False):
            continue
        retail = row.get("Proposed Retail Price") or row.get("Suggested Retail Price")
        if max_retail is not None and (retail is None or retail > max_retail):
            continue  # outside the affordable band
        if min_retail is not None and (retail is None or retail < min_retail):
            continue
        supplier_link = row.get("Supplier") or []
        supplier_key = supplier_key_by_id.get(supplier_link[0]) if supplier_link else ""
        collection = row.get("Collection") or Collection.UNCLASSIFIED.value
        # Skip non-target collections: Unclassified and the retired Practical Finds catch-all.
        if collection in (Collection.UNCLASSIFIED.value, Collection.PRACTICAL_FINDS.value):
            continue
        candidates.append(
            Candidate(
                product_key=row.get("Product Key", ""),
                supplier_key=supplier_key or "unknown",
                collection=Collection(collection),
                product_score=row.get("Product Score") or 0.0,
                price=retail,
                content_potential=_content_potential(row),
                name=row.get("Product Name") or "",
            )
        )
    return candidates


def _batch_id(batch_type: BatchType) -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    slug = batch_type.value.lower().replace(" ", "-")
    return f"{slug}-{stamp}-{uuid.uuid4().hex[:6]}"


def _create_batch(
    persistence,
    result: SelectionResult,
    *,
    batch_type: BatchType,
    candidate_status: SelectionStatus,
) -> dict:
    """Persist a Selection Batch + mark chosen products as candidates."""
    key_to_row = {row.get("Product Key"): row["id"] for row in persistence.iter_products()}
    product_row_ids = [
        key_to_row[c.product_key] for c in result.selected if c.product_key in key_to_row
    ]

    per = result.per_collection
    fields = {
        "Batch ID": _batch_id(batch_type),
        "Batch Type": batch_type.value,
        "Status": BatchStatus.DRAFT.value,
        "Created At": _now(),
        "Product Count": result.count,
        "Kitchen Convenience Count": per.get(Collection.KITCHEN_CONVENIENCE, 0),
        "Home Comfort Count": per.get(Collection.HOME_COMFORT, 0),
        "Practical Finds Count": per.get(Collection.PRACTICAL_FINDS, 0),
        "Notes": "\n".join(result.notes),
    }
    batch_row_id = persistence.create_selection_batch(fields, product_row_ids)

    for c in result.selected:
        row_id = key_to_row.get(c.product_key)
        if row_id is not None:
            persistence.update_product(row_id, {"Selection Status": candidate_status.value})

    return {"batch_id": fields["Batch ID"], "batch_row_id": batch_row_id, "result": result}


def _reset_candidates(persistence, candidate_status: SelectionStatus) -> int:
    """Clear a prior *candidate* status back to Not Selected before a fresh selection.

    Never touches manually-*Selected*/Published products — only pending candidates from an
    earlier run, so re-running selection doesn't accumulate stale candidates.
    """
    resets = [
        {"id": row["id"], "Selection Status": SelectionStatus.NOT_SELECTED.value}
        for row in persistence.iter_products()
        if row.get("Selection Status") == candidate_status.value
    ]
    persistence.update_product_rows(resets)
    return len(resets)


def make_initial_assortment(persistence, config: AppConfig) -> dict:
    """Create the initial assortment candidate batch (spec §26)."""
    _reset_candidates(persistence, SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE)
    result = select_initial(build_candidates(persistence, config=config), config)
    return _create_batch(
        persistence, result,
        batch_type=BatchType.INITIAL_ASSORTMENT,
        candidate_status=SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE,
    )


def make_new_arrivals(persistence, config: AppConfig) -> dict:
    """Create a new-arrivals candidate batch (spec §29)."""
    _reset_candidates(persistence, SelectionStatus.NEW_ARRIVAL_CANDIDATE)
    candidates = build_candidates(persistence, only_new=True, config=config)
    result = select_new_arrivals(candidates, config)
    return _create_batch(
        persistence, result,
        batch_type=BatchType.NEW_ARRIVALS,
        candidate_status=SelectionStatus.NEW_ARRIVAL_CANDIDATE,
    )


def _candidate_from_row(row: dict, supplier_key_by_id: dict) -> Candidate:
    link = row.get("Supplier") or []
    supplier_key = supplier_key_by_id.get(link[0]) if link else ""
    return Candidate(
        product_key=row.get("Product Key", ""),
        supplier_key=supplier_key or "unknown",
        collection=Collection(row.get("Collection")),
        product_score=row.get("Product Score") or 0.0,
        price=row.get("Proposed Retail Price") or row.get("Suggested Retail Price"),
        content_potential=_content_potential(row),
        name=row.get("Product Name") or "",
    )


def _reject_product(persistence, row: dict, note: str) -> None:
    """Mark a product Manually Rejected + Not Selected, with an immutable audit row (§14)."""
    persistence.update_product(row["id"], {
        "Review Status": ProductReviewStatus.MANUALLY_REJECTED.value,
        "Selection Status": SelectionStatus.NOT_SELECTED.value,
        "Manual Notes": note,
    })
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    persistence.create_manual_decision({
        "Decision ID": f"decision-{stamp}-{uuid.uuid4().hex[:6]}",
        "Entity Type": EntityType.PRODUCT.value,
        "Product": [row["id"]],
        "Previous Status": row.get("Review Status", ""),
        "New Status": ProductReviewStatus.MANUALLY_REJECTED.value,
        "Decision": DecisionValue.REJECT.value,
        "Reason": note,
        "Decided At": _now(),
        "Decided By": "cli",
    })


def reject_and_backfill(
    persistence, config: AppConfig, reject_keys, *, note: str = "Manual replace",
    candidate_status: SelectionStatus = SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE,
) -> dict:
    """Reject the given products and backfill their collections from the next-best candidates.

    Surgical: the *kept* candidates are seeded into the diversity state and never touched, so
    the rest of the assortment (incl. manual pins) is preserved. Only under-target collections
    are refilled, respecting the same hard diversity + price-band constraints as selection.
    """
    reject_set = set(reject_keys)
    by_key = {r.get("Product Key"): r for r in persistence.iter_products()}
    for key in reject_keys:
        if key in by_key:
            _reject_product(persistence, by_key[key], note)

    rows = persistence.iter_products()
    supplier_key_by_id = {r["id"]: r.get("Supplier Key") for r in persistence.iter_suppliers()}
    kept = [r for r in rows
            if r.get("Selection Status") == candidate_status.value
            and r.get("Product Key") not in reject_set]
    state = SelectionState()
    for r in kept:
        state.accept(_candidate_from_row(r, supplier_key_by_id))

    sel = config.selection
    max_r, min_r = sel.max_retail_price, sel.min_retail_price
    pool: list[tuple[dict, Candidate]] = []
    for r in rows:
        if r.get("Review Status") != ProductReviewStatus.SHORTLISTED.value:
            continue
        if r.get("Selection Status") not in (None, SelectionStatus.NOT_SELECTED.value):
            continue
        if r.get("Product Key") in reject_set:
            continue
        retail = r.get("Proposed Retail Price") or r.get("Suggested Retail Price")
        if max_r is not None and (retail is None or retail > max_r):
            continue
        if min_r is not None and (retail is None or retail < min_r):
            continue
        coll = r.get("Collection")
        if not coll or coll == Collection.UNCLASSIFIED.value:
            continue
        pool.append((r, _candidate_from_row(r, supplier_key_by_id)))

    per_max = sel.target_per_collection_max
    per_sup = max_products_per_supplier(sel.initial_total_max, sel.max_supplier_share_pct)
    chosen: dict[str, dict] = {}
    while True:
        best = None
        for r, c in pool:
            if c.product_key in chosen:
                continue
            if state.per_collection.get(c.collection, 0) >= per_max:
                continue
            if violates_hard_constraints(
                c, state, per_collection_max=per_max, per_supplier_max=per_sup
            ):
                continue
            score = selection_score(
                c, state, per_collection_target_min=sel.target_per_collection_min
            )
            if best is None or score > best[0]:
                best = (score, r, c)
        if not best:
            break
        _, r, c = best
        state.accept(c)
        chosen[c.product_key] = r

    if chosen:
        persistence.update_product_rows(
            [{"id": r["id"], "Selection Status": candidate_status.value} for r in chosen.values()]
        )
    return {
        "rejected": list(reject_keys),
        "added": list(chosen.keys()),
        "added_rows": list(chosen.values()),
        "per_collection": {k.value: v for k, v in state.per_collection.items()},
    }
