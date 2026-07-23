"""Scoring & classification orchestration (spec §20–§25).

Reads suppliers and products from a :class:`~..runs.persistence.ReviewOps` backend, scores
suppliers first (spec §20), excludes products of rejected suppliers (spec §21), then scores
and classifies the survivors (spec §22–§25), writing statuses/scores/reason codes back.

Records are re-normalized from the stored ``Raw Data`` so scoring reuses the exact same
tested normalization the scanner used — no field re-parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..config import AppConfig
from ..extraction.records import normalize_product, normalize_supplier
from ..models import ProductReviewStatus, SupplierEligibility
from .product_score import score_product
from .reason_codes import encode
from .supplier_score import SupplierScore, score_supplier, supplier_blocks_products

# Manual review decisions are authoritative (spec §14) — re-scoring must never overwrite them.
_MANUAL_REVIEW_STATES = frozenset({
    ProductReviewStatus.MANUALLY_REJECTED.value,
    ProductReviewStatus.APPROVED.value,
})


@dataclass
class SupplierScoringSummary:
    scored: int = 0
    approved: int = 0
    manual_review: int = 0
    rejected: int = 0
    gate_failed: int = 0
    blocked: int = 0


@dataclass
class ProductScoringSummary:
    scored: int = 0
    shortlisted: int = 0
    manual_review: int = 0
    rejected: int = 0
    excluded_by_supplier: int = 0
    gate_failed: int = 0
    manual_preserved: int = 0
    by_collection: dict[str, int] = field(default_factory=dict)


def _raw(row: dict) -> dict:
    data = row.get("Raw Data")
    if isinstance(data, str) and data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data or {}


def score_suppliers(persistence, config: AppConfig) -> SupplierScoringSummary:
    """Score every supplier and persist eligibility (spec §20). Returns a summary."""
    summary = SupplierScoringSummary()
    updates: list[dict] = []
    for row in persistence.iter_suppliers():
        raw = _raw(row)
        norm = normalize_supplier(raw) if raw else {"supplier_key": row.get("Supplier Key")}
        norm["relevant_product_count"] = row.get("Relevant Product Count") or 0
        if not raw:  # fall back to a couple stored fields when Raw Data is absent
            norm.setdefault("active", bool(row.get("Active", True)))

        result = score_supplier(norm, config, manual_override=row.get("Manual Override"))
        updates.append({"id": row["id"], **_supplier_fields(result)})
        _tally_supplier(summary, result)
    persistence.update_supplier_rows(updates)
    return summary


def score_products(persistence, config: AppConfig) -> ProductScoringSummary:
    """Score + classify products for eligible suppliers (spec §21–§25)."""
    from ..classification.rules import classify_product

    # Build supplier row-id -> (eligibility, score) map from the just-scored suppliers.
    supplier_state: dict[int, tuple[SupplierEligibility, float]] = {}
    for row in persistence.iter_suppliers():
        elig = row.get("Eligibility Status")
        supplier_state[row["id"]] = (
            SupplierEligibility(elig) if elig else SupplierEligibility.UNSCORED,
            row.get("Supplier Score") or 0.0,
        )

    summary = ProductScoringSummary()
    updates: list[dict] = []
    for row in persistence.iter_products():
        if row.get("Review Status") in _MANUAL_REVIEW_STATES:
            summary.manual_preserved += 1
            continue  # never overwrite a manual approve/reject decision
        supplier_link = row.get("Supplier") or []
        supplier_row_id = supplier_link[0] if supplier_link else None
        eligibility, supplier_score = supplier_state.get(
            supplier_row_id, (SupplierEligibility.UNSCORED, 0.0)
        )
        supplier_eligible = not supplier_blocks_products(eligibility)

        raw = _raw(row)
        skey = row.get("Product Key", "")
        supplier_key = ""
        if raw.get("supplier"):
            supplier_key = normalize_supplier(raw["supplier"]).get("supplier_key", "")
        norm = (
            normalize_product(
                raw, supplier_key_value=supplier_key,
                target_codes=config.markets.target_codes,
            )
            if raw else {"product_key": skey}
        )
        # Raw Data doesn't carry the scanned subcategory; use the persisted field so
        # classification can map it to a collection reliably.
        persisted_subcat = row.get("Syncee Subcategory")
        if persisted_subcat:
            norm["syncee_subcategory"] = persisted_subcat

        pscore = score_product(
            norm, config, supplier_eligible=supplier_eligible, supplier_score=supplier_score
        )
        classification = classify_product(norm, config)
        updates.append({
            "id": row["id"], **_product_fields(pscore, classification, supplier_eligible),
        })
        _tally_product(summary, pscore, classification)
    persistence.update_product_rows(updates)
    return summary


# --- Field builders ----------------------------------------------------------------


def _supplier_fields(r: SupplierScore) -> dict:
    return {
        "Hard Gate Status": r.gate_status.value,
        "Supplier Score": r.score,
        "Supplier Score Version": r.version,
        "Eligibility Status": r.eligibility.value,
        "Reason Codes": encode(r.reasons),
    }


def _product_fields(r, classification, supplier_eligible: bool) -> dict:
    m = r.margin
    fields = {
        "Supplier Eligible": supplier_eligible,
        "Product Gate Status": r.gate_status.value,
        "Product Score": r.score,
        "Product Score Version": r.version,
        "Review Status": r.review_status.value,
        "Margin Status": m.status.value,
        "Collection": classification.collection.value,
        "Classification Confidence": classification.confidence,
        "Exclusion Reason Codes": encode(r.reasons),
        "Risk Flags": ", ".join(r.risk_flags),
    }
    if m.landed_cost is not None:
        fields["Estimated Landed Cost"] = m.landed_cost
        fields["Estimated Margin Amount"] = m.margin_amount
        fields["Estimated Margin Pct"] = m.margin_pct
        fields["Proposed Retail Price"] = m.proposed_retail_price
        # Syncee's RRP converted to EUR, so it's comparable to the final EUR price.
        if m.market_price:
            fields["Market Price (EUR)"] = round(m.market_price, 2)
            # How far our price sits from the market: +% above RRP, -% below. Key has no '%'
            # so it's usable in SQL filters/sorts (a literal % breaks parameterized queries).
            if m.proposed_retail_price:
                fields["Price vs RRP"] = round(
                    (m.proposed_retail_price / m.market_price - 1) * 100, 1
                )
    return fields


# --- Tallies -----------------------------------------------------------------------


def _tally_supplier(s: SupplierScoringSummary, r: SupplierScore) -> None:
    s.scored += 1
    match r.eligibility:
        case SupplierEligibility.APPROVED | SupplierEligibility.MANUALLY_APPROVED:
            s.approved += 1
        case SupplierEligibility.MANUAL_REVIEW:
            s.manual_review += 1
        case SupplierEligibility.SCORED_REJECTED:
            s.rejected += 1
        case SupplierEligibility.GATE_FAILED:
            s.gate_failed += 1
        case SupplierEligibility.MANUALLY_BLOCKED:
            s.blocked += 1


def _tally_product(s: ProductScoringSummary, r, classification) -> None:
    from ..models import ProductReviewStatus

    s.scored += 1
    match r.review_status:
        case ProductReviewStatus.SHORTLISTED:
            s.shortlisted += 1
        case ProductReviewStatus.MANUAL_REVIEW:
            s.manual_review += 1
        case ProductReviewStatus.SCORED_REJECTED:
            s.rejected += 1
        case ProductReviewStatus.EXCLUDED_BY_SUPPLIER:
            s.excluded_by_supplier += 1
        case ProductReviewStatus.GATE_FAILED:
            s.gate_failed += 1
    key = classification.collection.value
    s.by_collection[key] = s.by_collection.get(key, 0) + 1
