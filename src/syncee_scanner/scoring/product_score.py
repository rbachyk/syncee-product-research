"""Product weighted scoring, versioning and review status (spec §24).

Combines structural hard gates, margin (spec §23), risk flags (spec §22.1) and a 0–100
weighted score into a final :class:`ProductReviewStatus`. High-risk or margin-incomplete
products are routed to manual review and never auto-shortlisted (spec §43.6). Every scored
product records its score version + reason codes + risk flags (spec §24.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import AppConfig
from ..models import HardGateStatus, MarginStatus, ProductReviewStatus
from .margin import MarginResult, compute_margin
from .product_gates import ProductGateResult, evaluate_product_gates, risk_reasons
from .reason_codes import ProductReason

_PROBLEM_WORDS = re.compile(
    r"\b(solve|solves|easy|quick|save|saves|organi[sz]e|reduce|prevent|no more|effortless)\b"
)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class ProductScore:
    product_key: str
    score: float
    version: str
    gate_status: HardGateStatus
    review_status: ProductReviewStatus
    margin: MarginResult
    reasons: list[ProductReason] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    criteria: dict[str, float] = field(default_factory=dict)

    @property
    def shortlisted(self) -> bool:
        return self.review_status == ProductReviewStatus.SHORTLISTED


# --- Criterion sub-scores ----------------------------------------------------------


def _problem_solved(product: dict) -> float:
    text = f"{product.get('product_name') or ''} {product.get('description') or ''}".lower()
    hits = len(_PROBLEM_WORDS.findall(text))
    return _clamp01(0.5 + 0.15 * hits)


def _margin_potential(margin: MarginResult, config: AppConfig) -> float:
    # In target-margin pricing every product hits target, so rank by how competitive the
    # target-margin price is vs Syncee's RRP (>=1 == at/below market -> full score).
    if margin.competitiveness is not None:
        return _clamp01(margin.competitiveness)
    if margin.margin_pct is None:
        return 0.0
    return _clamp01(margin.margin_pct / max(1.0, config.margin.target_margin_pct))


def _shipping(product: dict, threshold: int) -> float:
    """Shipping score = worst-case speed across target markets × market coverage.

    Uses the slowest target-market shipping time (spec: don't understate by only checking
    Ireland) and scales by how many of the target markets the product actually ships to.
    """
    max_days = product.get("shipping_max_days")
    speed = 0.3 if max_days is None else _clamp01(1.0 - max_days / (2 * max(1, threshold)))
    shipped = product.get("target_markets_shipped")
    total = product.get("target_markets_total")
    coverage = shipped / total if (shipped is not None and total) else 1.0
    return speed * coverage


def _content_potential(product: dict) -> float:
    images = len(product.get("image_urls") or [])
    desc_len = len(product.get("description") or "")
    return _clamp01(0.3 + 0.15 * images + min(0.4, desc_len / 600))


def _differentiation(product: dict) -> float:
    return 0.6 if (product.get("brand") or "").strip() else 0.45


def _return_risk(risk_flags: list[str]) -> float:
    from .product_gates import _REFUND_RISKS

    return 0.4 if any(f in _REFUND_RISKS for f in risk_flags) else 0.9


def _data_quality(product: dict) -> float:
    present = sum(
        1
        for key in ("product_name", "description", "main_image_url", "supplier_price", "currency")
        if product.get(key)
    )
    return _clamp01(present / 5.0)


def compute_product_criteria(
    product: dict, margin: MarginResult, supplier_score: float, risk_flags: list[str],
    config: AppConfig,
) -> dict[str, float]:
    """Compute each weighted-score criterion as a 0..1 fraction (spec §24.2)."""
    return {
        "problem_solved": _problem_solved(product),
        "margin": _margin_potential(margin, config),
        "shipping": _shipping(product, config.product_gates.max_shipping_days),
        "content_potential": _content_potential(product),
        "differentiation": _differentiation(product),
        "return_risk": _return_risk(risk_flags),
        "data_quality": _data_quality(product),
        "supplier_strength": _clamp01(supplier_score / 100.0),
    }


def weighted_score(criteria: dict[str, float], weights: dict[str, float]) -> float:
    return round(sum(criteria.get(k, 0.0) * w for k, w in weights.items()), 1)


def score_product(
    product: dict,
    config: AppConfig,
    *,
    supplier_eligible: bool,
    supplier_score: float = 0.0,
) -> ProductScore:
    """Full product evaluation → gate status, margin, score, review status (spec §24)."""
    cfg = config.product_scoring
    key = product.get("product_key", "")
    gate: ProductGateResult = evaluate_product_gates(
        product, config, supplier_eligible=supplier_eligible
    )
    margin = compute_margin(product, config)
    flags = gate.risk_flags
    reasons: list[ProductReason] = list(gate.reasons)

    # Excluded by supplier / failed structural gates short-circuit (spec §21, §22).
    if gate.status == HardGateStatus.EXCLUDED_BY_SUPPLIER:
        return ProductScore(key, 0.0, cfg.version, gate.status,
                            ProductReviewStatus.EXCLUDED_BY_SUPPLIER, margin, reasons, flags)
    if not gate.passed:
        return ProductScore(key, 0.0, cfg.version, gate.status,
                            ProductReviewStatus.GATE_FAILED, margin, reasons, flags)

    criteria = compute_product_criteria(product, margin, supplier_score, flags, config)
    score = weighted_score(criteria, cfg.weights)

    # Risk flags and incomplete margin force manual review (spec §22.1, §23.4, §43.6).
    force_manual = False
    if flags:
        reasons.extend(risk_reasons(flags))
        force_manual = True
    if margin.status == MarginStatus.INCOMPLETE:
        reasons.append(ProductReason.MARGIN_UNKNOWN)
        force_manual = True
    elif margin.status == MarginStatus.BELOW_MINIMUM:
        reasons.append(ProductReason.LOW_MARGIN)
    if margin.shipping_estimated:
        # Informational only — an estimated margin can still shortlist; verify on review.
        reasons.append(ProductReason.MARGIN_ESTIMATED)
    if margin.uncompetitive:
        # Target-margin price is well above Syncee's RRP — informational, lowers the score.
        reasons.append(ProductReason.UNCOMPETITIVE_PRICE)

    if margin.status == MarginStatus.BELOW_MINIMUM:
        # Can't clear the minimum margin at its market price → not sellable profitably, reject.
        review = ProductReviewStatus.SCORED_REJECTED
    elif force_manual:
        review = ProductReviewStatus.MANUAL_REVIEW
    elif score < cfg.reject_below:
        reasons.append(ProductReason.LOW_PRODUCT_SCORE)
        review = ProductReviewStatus.SCORED_REJECTED
    elif cfg.shortlist_from is not None and score >= cfg.shortlist_from:
        review = ProductReviewStatus.SHORTLISTED
    else:
        review = ProductReviewStatus.MANUAL_REVIEW

    return ProductScore(
        key, score, cfg.version, HardGateStatus.PASSED, review, margin, reasons, flags, criteria
    )
