"""Supplier hard gates (spec §20.2, §20.4).

Hard gates are pass/fail eligibility filters evaluated before weighted scoring. A failed
hard gate cannot be ignored automatically — only an explicit manual override may change
final eligibility (spec §20.8). This module is pure: it takes a normalized supplier dict
plus config and returns the failed reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AppConfig
from .reason_codes import SupplierReason


@dataclass
class GateResult:
    passed: bool
    reasons: list[SupplierReason] = field(default_factory=list)


def evaluate_supplier_gates(supplier: dict, config: AppConfig) -> GateResult:
    """Evaluate all supplier hard gates (spec §20.2). Returns pass/fail + reasons."""
    gates = config.supplier_gates
    targets = {c.lower() for c in config.markets.target}
    reasons: list[SupplierReason] = []

    if not supplier.get("active", True):
        reasons.append(SupplierReason.INACTIVE)

    # 1. ships to at least one target market — but only HARD-fail when we actually KNOW the
    # ship-to list and it excludes every target. Empty/unknown ships_to (common in sparse list
    # data — ~30% complete) is NOT a rejection; the weighted score penalizes unknowns instead.
    ships_to = {c.lower() for c in (supplier.get("ships_to_countries") or [])}
    if gates.require_target_market and ships_to and not (ships_to & targets):
        reasons.append(SupplierReason.NO_TARGET_MARKET)

    # 1b. dispatches from an allowed (EU) country — avoids import VAT/customs, faster delivery
    if gates.require_dispatch_in_allowed:
        allowed = {c.lower() for c in config.markets.dispatch_allowed}
        dispatch = {
            c.lower()
            for c in ([supplier.get("location_country")] or [])
            + (supplier.get("dispatch_countries") or [])
            if c
        }
        if not (dispatch & allowed):
            reasons.append(SupplierReason.DISPATCH_OUTSIDE_EUROPE)

    # 2. shipping time within configured maximum
    max_days = supplier.get("shipping_max_days")
    if max_days is None:
        # Unknown shipping only hard-fails when explicitly required (spec §20.2 gate 6);
        # otherwise the weighted score penalizes it instead of excluding the supplier.
        if gates.require_known_shipping:
            reasons.append(SupplierReason.SHIPPING_UNKNOWN)
    elif max_days > gates.max_shipping_days:
        reasons.append(SupplierReason.SHIPPING_TOO_SLOW)

    # 3. at least one active relevant product
    if (supplier.get("relevant_product_count") or 0) < 1:
        reasons.append(SupplierReason.INSUFFICIENT_RELEVANT_PRODUCTS)

    # 4. identity sufficiently complete
    completeness = supplier.get("data_completeness_pct")
    if completeness is not None and completeness < gates.minimum_data_completeness_pct:
        reasons.append(SupplierReason.LOW_DATA_COMPLETENESS)

    # 6. required policies when configured
    if gates.require_shipping_policy and not supplier.get("shipping_policy_available"):
        reasons.append(SupplierReason.NO_SHIPPING_POLICY)
    if gates.require_return_policy and not supplier.get("return_policy_available"):
        reasons.append(SupplierReason.NO_RETURN_POLICY)

    return GateResult(passed=not reasons, reasons=reasons)
