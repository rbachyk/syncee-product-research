"""Supplier weighted scoring and eligibility (spec §20.5–§20.8).

Combines hard gates, a normalized 0–100 weighted score, thresholds and any manual override
into a final :class:`SupplierEligibility`. Deterministic and versioned: the same inputs +
config always yield the same score (spec §37.6), and the score version is stored so config
changes make suppliers eligible for rescoring (spec §5.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AppConfig
from ..models import HardGateStatus, ManualOverride, SupplierEligibility
from .reason_codes import SupplierReason
from .supplier_gates import evaluate_supplier_gates

# Supplier eligibility statuses whose products must be excluded (spec §21).
EXCLUDING_ELIGIBILITY = frozenset(
    {
        SupplierEligibility.GATE_FAILED,
        SupplierEligibility.SCORED_REJECTED,
        SupplierEligibility.MANUALLY_BLOCKED,
        SupplierEligibility.INACTIVE,
    }
)


def supplier_blocks_products(eligibility: SupplierEligibility) -> bool:
    """Whether products from a supplier in this state are excluded (spec §21)."""
    return eligibility in EXCLUDING_ELIGIBILITY


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class SupplierScore:
    supplier_key: str
    score: float
    version: str
    gate_status: HardGateStatus
    eligibility: SupplierEligibility
    reasons: list[SupplierReason] = field(default_factory=list)
    criteria: dict[str, float] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return self.eligibility in {
            SupplierEligibility.APPROVED,
            SupplierEligibility.MANUALLY_APPROVED,
        }


# --- Criterion sub-scores (each returns a 0..1 fraction) ---------------------------


def _market_coverage(supplier: dict, targets: set[str]) -> float:
    if not targets:
        return 0.0
    ships_to = {c.lower() for c in (supplier.get("ships_to_countries") or [])}
    return _clamp01(len(ships_to & targets) / len(targets))


def _shipping_speed(supplier: dict, max_days_threshold: int) -> float:
    max_days = supplier.get("shipping_max_days")
    if max_days is None:
        return 0.0
    # 1.0 for instant, linearly to 0.0 at twice the gate threshold.
    return _clamp01(1.0 - max_days / (2 * max(1, max_days_threshold)))


def _dispatch_proximity(supplier: dict, targets: set[str]) -> float:
    dispatch = {c.lower() for c in (supplier.get("dispatch_countries") or [])}
    if dispatch & targets:
        return 1.0
    location = (supplier.get("location_country") or "").lower()
    return 1.0 if location in targets else 0.0


def _tri(value: bool | None) -> float:
    """True -> 1.0, False -> 0.0, unknown (None) -> neutral 0.5.

    Syncee's data doesn't expose shipping/return-policy flags, so unknown must not be
    penalized as an outright absence (that would reject nearly every supplier).
    """
    if value is None:
        return 0.5
    return 1.0 if value else 0.0


def _rating(supplier: dict) -> float:
    rating = supplier.get("supplier_rating")
    if rating is None:
        return 0.5  # unknown rating is neutral, not a zero
    base = _clamp01(rating / 5.0)
    # Dampen when very few reviews back the rating.
    reviews = supplier.get("review_count") or 0
    confidence = _clamp01(reviews / 20.0)
    return base * (0.5 + 0.5 * confidence)


def _catalog_depth(supplier: dict) -> float:
    return _clamp01((supplier.get("relevant_product_count") or 0) / 5.0)


def compute_criteria(supplier: dict, config: AppConfig) -> dict[str, float]:
    """Compute each weighted-score criterion as a 0..1 fraction (spec §20.5)."""
    targets = {c.lower() for c in config.markets.target}
    return {
        "market_coverage": _market_coverage(supplier, targets),
        "shipping_speed": _shipping_speed(supplier, config.supplier_gates.max_shipping_days),
        "dispatch_proximity": _dispatch_proximity(supplier, targets),
        "data_completeness": _clamp01((supplier.get("data_completeness_pct") or 0) / 100.0),
        "shipping_policy": _tri(supplier.get("shipping_policy_available")),
        "return_policy": _tri(supplier.get("return_policy_available")),
        "rating": _rating(supplier),
        "catalog_depth": _catalog_depth(supplier),
        "approval_friction": 0.0 if supplier.get("approval_required") else 1.0,
    }


def weighted_score(criteria: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted 0–100 score; weights sum to 100 so this lands in [0, 100]."""
    return round(sum(criteria.get(k, 0.0) * w for k, w in weights.items()), 1)


def score_supplier(
    supplier: dict, config: AppConfig, *, manual_override: str | None = None
) -> SupplierScore:
    """Full supplier evaluation → gate status, score, eligibility (spec §20.5–§20.8)."""
    cfg = config.supplier_scoring
    key = supplier.get("supplier_key", "")
    override = (manual_override or ManualOverride.NONE.value)

    gate = evaluate_supplier_gates(supplier, config)
    criteria = compute_criteria(supplier, config)
    score = weighted_score(criteria, cfg.weights)
    reasons: list[SupplierReason] = list(gate.reasons)
    gate_status = HardGateStatus.PASSED if gate.passed else HardGateStatus.FAILED

    # Manual override wins over automated gate/score outcomes (spec §20.8).
    if override == ManualOverride.BLOCK.value:
        reasons.append(SupplierReason.MANUALLY_BLOCKED)
        return SupplierScore(key, score, cfg.version, gate_status,
                             SupplierEligibility.MANUALLY_BLOCKED, reasons, criteria)
    if override == ManualOverride.APPROVE.value:
        reasons.append(SupplierReason.MANUALLY_APPROVED)
        return SupplierScore(key, score, cfg.version, gate_status,
                             SupplierEligibility.MANUALLY_APPROVED, reasons, criteria)

    if not supplier.get("active", True):
        return SupplierScore(key, score, cfg.version, gate_status,
                             SupplierEligibility.INACTIVE, reasons, criteria)

    if not gate.passed:
        return SupplierScore(key, score, cfg.version, HardGateStatus.FAILED,
                             SupplierEligibility.GATE_FAILED, reasons, criteria)

    # Passed gates → apply score thresholds (spec §20.6).
    if score < cfg.reject_below:
        reasons.append(SupplierReason.LOW_SUPPLIER_SCORE)
        eligibility = SupplierEligibility.SCORED_REJECTED
    elif cfg.approve_from is not None and score >= cfg.approve_from:
        eligibility = SupplierEligibility.APPROVED
    else:
        eligibility = SupplierEligibility.MANUAL_REVIEW
    if supplier.get("approval_required"):
        reasons.append(SupplierReason.APPROVAL_REQUIRED)

    return SupplierScore(key, score, cfg.version, gate_status, eligibility, reasons, criteria)
