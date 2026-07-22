"""Unit tests for supplier gates + scoring + eligibility (spec §20, §21, §41.1)."""

from syncee_scanner.config import load_config
from syncee_scanner.models import ManualOverride, SupplierEligibility
from syncee_scanner.scoring.reason_codes import SupplierReason
from syncee_scanner.scoring.supplier_gates import evaluate_supplier_gates
from syncee_scanner.scoring.supplier_score import (
    score_supplier,
    supplier_blocks_products,
)


def cfg():
    return load_config()


def strong_supplier(**over) -> dict:
    base = {
        "supplier_key": "sid:1",
        "ships_to_countries": ["Germany", "France", "Spain", "Italy"],
        "dispatch_countries": ["Germany"],
        "location_country": "Germany",
        "shipping_max_days": 5,
        "shipping_min_days": 2,
        "relevant_product_count": 8,
        "data_completeness_pct": 90.0,
        "shipping_policy_available": True,
        "return_policy_available": True,
        "supplier_rating": 4.7,
        "review_count": 100,
        "approval_required": False,
        "active": True,
    }
    base.update(over)
    return base


class TestGates:
    def test_strong_supplier_passes(self):
        assert evaluate_supplier_gates(strong_supplier(), cfg()).passed

    def test_no_target_market_fails(self):
        c = cfg()
        c.supplier_gates.require_target_market = True  # soft by default; enable to test the gate
        r = evaluate_supplier_gates(strong_supplier(ships_to_countries=["United States"]), c)
        assert not r.passed
        assert SupplierReason.NO_TARGET_MARKET in r.reasons

    def test_empty_ships_to_does_not_fail(self):
        # Unknown/empty ships_to (sparse list data) must NOT hard-fail — only known-and-excluding.
        c = cfg()
        c.supplier_gates.require_target_market = True
        r = evaluate_supplier_gates(strong_supplier(ships_to_countries=[]), c)
        assert SupplierReason.NO_TARGET_MARKET not in r.reasons

    def test_slow_shipping_fails(self):
        r = evaluate_supplier_gates(strong_supplier(shipping_max_days=30), cfg())
        assert SupplierReason.SHIPPING_TOO_SLOW in r.reasons

    def test_unknown_shipping_does_not_fail_by_default(self):
        # Syncee's list API lacks shipping days; default config must not hard-fail (item D).
        r = evaluate_supplier_gates(strong_supplier(shipping_max_days=None), cfg())
        assert SupplierReason.SHIPPING_UNKNOWN not in r.reasons
        assert r.passed

    def test_unknown_shipping_fails_when_required(self):
        c = cfg()
        c.supplier_gates.require_known_shipping = True
        r = evaluate_supplier_gates(strong_supplier(shipping_max_days=None), c)
        assert SupplierReason.SHIPPING_UNKNOWN in r.reasons

    def test_no_relevant_products_fails(self):
        r = evaluate_supplier_gates(strong_supplier(relevant_product_count=0), cfg())
        assert SupplierReason.INSUFFICIENT_RELEVANT_PRODUCTS in r.reasons

    def test_low_completeness_fails(self):
        r = evaluate_supplier_gates(strong_supplier(data_completeness_pct=10), cfg())
        assert SupplierReason.LOW_DATA_COMPLETENESS in r.reasons


class TestScore:
    def test_strong_supplier_approved(self):
        s = score_supplier(strong_supplier(), cfg())
        assert s.eligibility == SupplierEligibility.APPROVED
        assert s.score >= 75
        assert s.eligible is True

    def test_gate_failure_beats_score(self):
        c = cfg()
        c.supplier_gates.require_target_market = True  # gate is soft by default; enable it here
        s = score_supplier(strong_supplier(ships_to_countries=["United States"]), c)
        assert s.eligibility == SupplierEligibility.GATE_FAILED
        assert s.eligible is False

    def test_mid_score_manual_review(self):
        # Passes gates but weaker signals -> between reject_below and approve_from.
        weak = strong_supplier(
            shipping_max_days=9, data_completeness_pct=65, return_policy_available=False,
            shipping_policy_available=False, supplier_rating=3.0, review_count=2,
            ships_to_countries=["Germany"], relevant_product_count=1,
        )
        s = score_supplier(weak, cfg())
        assert s.eligibility in {
            SupplierEligibility.MANUAL_REVIEW,
            SupplierEligibility.SCORED_REJECTED,
        }

    def test_manual_block_overrides(self):
        s = score_supplier(strong_supplier(), cfg(), manual_override=ManualOverride.BLOCK.value)
        assert s.eligibility == SupplierEligibility.MANUALLY_BLOCKED

    def test_manual_approve_overrides_gate_failure(self):
        c = cfg()
        c.supplier_gates.require_target_market = True
        s = score_supplier(
            strong_supplier(ships_to_countries=["United States"]), c,
            manual_override=ManualOverride.APPROVE.value,
        )
        assert s.eligibility == SupplierEligibility.MANUALLY_APPROVED
        assert s.eligible is True

    def test_deterministic(self):
        a = score_supplier(strong_supplier(), cfg())
        b = score_supplier(strong_supplier(), cfg())
        assert a.score == b.score
        assert a.version == cfg().supplier_scoring.version


class TestExclusion:
    def test_excluding_states(self):
        assert supplier_blocks_products(SupplierEligibility.GATE_FAILED)
        assert supplier_blocks_products(SupplierEligibility.SCORED_REJECTED)
        assert supplier_blocks_products(SupplierEligibility.MANUALLY_BLOCKED)
        assert supplier_blocks_products(SupplierEligibility.INACTIVE)

    def test_non_excluding_states(self):
        assert not supplier_blocks_products(SupplierEligibility.APPROVED)
        assert not supplier_blocks_products(SupplierEligibility.MANUAL_REVIEW)
        assert not supplier_blocks_products(SupplierEligibility.MANUALLY_APPROVED)


class TestEuDispatchGate:
    def test_non_eu_dispatch_fails_when_required(self):
        c = cfg()
        c.supplier_gates.require_dispatch_in_allowed = True
        r = evaluate_supplier_gates(
            strong_supplier(location_country="Australia", dispatch_countries=["Australia"]), c
        )
        assert SupplierReason.DISPATCH_OUTSIDE_EUROPE in r.reasons

    def test_eu_dispatch_passes(self):
        c = cfg()
        c.supplier_gates.require_dispatch_in_allowed = True
        r = evaluate_supplier_gates(
            strong_supplier(location_country="Germany", dispatch_countries=["Germany"]), c
        )
        assert SupplierReason.DISPATCH_OUTSIDE_EUROPE not in r.reasons

    def test_gate_off_ignores_dispatch(self):
        c = cfg()
        c.supplier_gates.require_dispatch_in_allowed = False
        r = evaluate_supplier_gates(strong_supplier(location_country="Australia"), c)
        assert SupplierReason.DISPATCH_OUTSIDE_EUROPE not in r.reasons
