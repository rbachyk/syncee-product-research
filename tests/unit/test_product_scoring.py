"""Unit tests for margin, product gates, scoring and classification (spec §22–§25)."""

from syncee_scanner.classification.rules import classify_product
from syncee_scanner.config import load_config
from syncee_scanner.models import Collection, MarginStatus, ProductReviewStatus
from syncee_scanner.scoring.margin import compute_margin
from syncee_scanner.scoring.product_gates import detect_risk_flags, evaluate_product_gates
from syncee_scanner.scoring.product_score import score_product
from syncee_scanner.scoring.reason_codes import ProductReason


def cfg():
    return load_config()


def good_product(**over) -> dict:
    base = {
        "product_key": "pid:1",
        "product_name": "Stainless Steel Garlic Press for easy cooking",
        "description": "Solves garlic prep, quick and easy to organize your kitchen. " * 5,
        "syncee_category": "Home & Kitchen",
        "syncee_subcategory": "Kitchen Tools",
        "supplier_price": 4.5,
        "shipping_cost": 2.0,
        "shipping_cost_known": True,
        "suggested_retail_price": 24.9,
        "proposed_retail_price": 24.9,
        "shipping_max_days": 6,
        "stock_status": "In Stock",
        "stock_quantity": 100,
        "main_image_url": "https://x/1.jpg",
        "image_urls": ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"],
        "currency": "EUR",
        "brand": "KitchenPro",
        "active": True,
    }
    base.update(over)
    return base


class TestMargin:
    def test_target_met(self):
        m = compute_margin(good_product(), cfg())
        assert m.status in {MarginStatus.TARGET_MET, MarginStatus.ACCEPTABLE}
        assert m.margin_pct is not None and m.margin_pct > 0

    def test_estimates_shipping_when_unknown(self):
        # Default config estimates shipping so margin still computes (flagged estimated).
        m = compute_margin(good_product(shipping_cost_known=False, shipping_cost=None), cfg())
        assert m.status != MarginStatus.INCOMPLETE
        assert m.margin_pct is not None
        assert m.shipping_estimated is True

    def test_incomplete_when_estimation_disabled(self):
        c = cfg()
        c.margin.estimate_shipping_when_unknown = False
        m = compute_margin(good_product(shipping_cost_known=False, shipping_cost=None), c)
        assert m.status == MarginStatus.INCOMPLETE

    def test_incomplete_when_no_retail(self):
        m = compute_margin(
            good_product(suggested_retail_price=None, proposed_retail_price=None), cfg()
        )
        assert m.status == MarginStatus.INCOMPLETE

    def test_below_minimum_at_rrp(self):
        c = cfg()
        c.margin.pricing_mode = "rrp"  # evaluate at Syncee RRP
        m = compute_margin(good_product(supplier_price=20.0, proposed_retail_price=24.9), c)
        assert m.status == MarginStatus.BELOW_MINIMUM

    def test_target_margin_pricing_hits_target(self):
        # Default target_margin mode prices to hit the target margin regardless of RRP.
        c = cfg()
        m = compute_margin(good_product(), c)
        assert m.status == MarginStatus.TARGET_MET
        assert m.margin_pct >= c.margin.target_margin_pct
        assert m.competitiveness is not None  # priced vs RRP


class TestRisk:
    def test_detects_heating(self):
        flags = detect_risk_flags({"product_name": "Electric heating blanket", "description": ""})
        assert "HEATING" in flags or "ELECTRICAL" in flags

    def test_clean_product_no_flags(self):
        assert detect_risk_flags(good_product()) == []

    def test_detects_character_trademarks(self):
        for name in ["Super Mario beach towel", "Spiderman quick-dry towel",
                     "Pokémon lunchbox", "Hello Kitty mug"]:
            assert "TRADEMARK" in detect_risk_flags({"product_name": name, "description": ""}), name


class TestGates:
    def test_good_product_passes(self):
        r = evaluate_product_gates(good_product(), cfg(), supplier_eligible=True)
        assert r.passed

    def test_excluded_when_supplier_ineligible(self):
        r = evaluate_product_gates(good_product(), cfg(), supplier_eligible=False)
        assert r.status.value == "Excluded by Supplier"
        assert ProductReason.SUPPLIER_REJECTED in r.reasons

    def test_missing_image_fails(self):
        r = evaluate_product_gates(
            good_product(main_image_url=None, image_urls=[]), cfg(), supplier_eligible=True
        )
        assert ProductReason.INSUFFICIENT_IMAGES in r.reasons

    def test_out_of_stock_fails(self):
        r = evaluate_product_gates(
            good_product(stock_status="Out Of Stock", stock_quantity=0),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.OUT_OF_STOCK in r.reasons

    def test_food_consumable_excluded(self):
        r = evaluate_product_gates(
            good_product(product_name="Organic Extra Virgin Olive Oil 500ml",
                         description="Cold-pressed olive oil from centenary trees."),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons
        assert not r.passed

    def test_essential_oil_excluded(self):
        r = evaluate_product_gates(
            good_product(product_name="Helichrysum essential oil 10ml"),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons

    def test_excluded_by_category_label(self):
        # Title hides it ("Just Married!"), but the supplier category "Kerzen" gives it away.
        r = evaluate_product_gates(
            good_product(product_name="Just Married!", description="Ein frischer Duft.",
                         syncee_category="Kerzen", syncee_subcategory="Lighting"),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons

    def test_tealights_excluded(self):
        r = evaluate_product_gates(
            good_product(product_name="Amber Dragon Duft-Teelichter", syncee_category="teelichter"),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons

    def test_refill_only_excluded(self):
        r = evaluate_product_gates(
            good_product(product_name="Pro-Tect 200ml TRIGGER REPLACEMENT ONLY"),
            cfg(), supplier_eligible=True,
        )
        assert ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons

    def test_normal_kitchen_product_not_excluded(self):
        r = evaluate_product_gates(good_product(), cfg(), supplier_eligible=True)
        assert ProductReason.EXCLUDED_PRODUCT_TYPE not in r.reasons

    def _excluded(self, name: str) -> bool:
        r = evaluate_product_gates(
            good_product(product_name=name, description=""), cfg(), supplier_eligible=True
        )
        return ProductReason.EXCLUDED_PRODUCT_TYPE in r.reasons

    def test_consumables_and_skincare_excluded(self):
        for name in [
            "Scented Wax Melts pack of 6",
            "Satya Juego de Incienso (incense set)",
            "Reed Diffuser Damask Rose 200ml",
            "Pro-Tect Anti Moustique insect spray",
            "Lavender Body Lotion 250ml",
            "Nourishing Hand Cream",
            "Natural Bar Soap set",
            "Aromatherapy Bath Bomb gift box",
            "Bergamood 155 g Kerze",
            "Bougie parfumée à la vanille",
        ]:
            assert self._excluded(name), name

    def test_durable_accessories_not_excluded(self):
        # Negative-lookahead guards: these are durable goods, must survive.
        for name in [
            "Cast Iron Candle Holder set of 3",
            "Ceramic Soap Dispenser for bathroom",
            "Cotton Bath Towel 70x140",
            "Cream White Cushion Cover 45x45",
            "Olive Wood Serving Board",
            "Decorative Perfume Bottle glass vase",
            "Mosquito Net canopy for bed",
            "Kerzenhalter aus Messing (brass candle holder)",
        ]:
            assert not self._excluded(name), name


class TestScore:
    def test_good_product_shortlisted(self):
        s = score_product(good_product(), cfg(), supplier_eligible=True, supplier_score=85)
        assert s.review_status == ProductReviewStatus.SHORTLISTED
        assert s.score >= 75

    def test_incomplete_margin_forces_manual_review(self):
        # Truly incomplete (estimation disabled) -> manual review.
        c = cfg()
        c.margin.estimate_shipping_when_unknown = False
        s = score_product(
            good_product(shipping_cost_known=False, shipping_cost=None),
            c, supplier_eligible=True, supplier_score=85,
        )
        assert s.review_status == ProductReviewStatus.MANUAL_REVIEW
        assert ProductReason.MARGIN_UNKNOWN in s.reasons

    def test_estimated_margin_can_still_shortlist(self):
        # Shipping unknown but estimated (default) -> can shortlist, flagged MARGIN_ESTIMATED.
        s = score_product(
            good_product(shipping_cost_known=False, shipping_cost=None),
            cfg(), supplier_eligible=True, supplier_score=85,
        )
        assert s.review_status == ProductReviewStatus.SHORTLISTED
        assert ProductReason.MARGIN_ESTIMATED in s.reasons

    def test_high_risk_never_auto_shortlisted(self):
        risky = good_product(product_name="Electric heater with lithium battery")
        s = score_product(risky, cfg(), supplier_eligible=True, supplier_score=85)
        assert s.review_status == ProductReviewStatus.MANUAL_REVIEW
        assert ProductReason.HIGH_COMPLIANCE_RISK in s.reasons

    def test_supplier_excluded_short_circuits(self):
        s = score_product(good_product(), cfg(), supplier_eligible=False)
        assert s.review_status == ProductReviewStatus.EXCLUDED_BY_SUPPLIER

    def test_deterministic(self):
        a = score_product(good_product(), cfg(), supplier_eligible=True, supplier_score=80)
        b = score_product(good_product(), cfg(), supplier_eligible=True, supplier_score=80)
        assert a.score == b.score


class TestClassification:
    def test_subcategory_map_used_when_configured(self):
        # Off by default (products classified by content); works when explicitly mapped.
        c = cfg()
        c.classification.category_collection_map = {"Eco-home": "Practical Finds"}
        r = classify_product({"product_name": "x", "syncee_subcategory": "Eco-home"}, c)
        assert r.collection == Collection.PRACTICAL_FINDS
        assert r.method == "subcategory-map"

    def test_subcategory_map_off_by_default(self):
        # With the empty default map, collection comes from content, not the subcategory.
        r = classify_product(
            {"product_name": "Cozy blanket", "syncee_subcategory": "Eco-home",
             "description": "warm cozy bedroom relax"},
            cfg(),
        )
        assert r.method != "subcategory-map"

    def test_category_map_kitchen(self):
        r = classify_product(good_product(), cfg())
        assert r.collection == Collection.KITCHEN_CONVENIENCE
        assert r.confidence >= 0.7

    def test_keyword_home_comfort(self):
        p = {"product_name": "Cozy bedroom throw blanket", "syncee_subcategory": "Textiles",
             "description": "warm and relaxing"}
        r = classify_product(p, cfg())
        assert r.collection == Collection.HOME_COMFORT

    def test_catch_all_practical_finds(self):
        p = {"product_name": "Abstract resin sculpture", "description": "a curio ornament"}
        r = classify_product(p, cfg())
        assert r.collection == Collection.PRACTICAL_FINDS
        assert r.needs_review is True
