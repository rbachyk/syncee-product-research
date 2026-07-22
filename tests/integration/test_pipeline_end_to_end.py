"""Full offline pipeline: scan -> score suppliers -> score products -> select (spec §43)."""

from syncee_scanner.config import load_config
from syncee_scanner.extraction.source import FixtureSource
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scan import run_scan
from syncee_scanner.scoring.service import score_products, score_suppliers
from syncee_scanner.selection.service import make_initial_assortment, make_new_arrivals


def cfg():
    return load_config()


def build_catalog() -> dict:
    """Synthetic Home & Kitchen catalog: several good suppliers + one that fails gates."""
    products = []
    collections = [
        ("Kitchen Tools", "Garlic Press for easy cooking", "Kitchen"),
        ("Bedroom", "Cozy warm throw blanket for relaxing", "Home & Kitchen"),
        ("Gadgets", "Handy multipurpose organizer solves clutter", "Home & Kitchen"),
    ]
    # 3 strong EU suppliers, each with several products across collections.
    for s in range(3):
        for i in range(4):
            sub, name, cat = collections[i % 3]
            products.append({
                "id": f"P{s}{i}",
                "name": f"{name} v{s}{i}",
                "url": f"https://app.syncee.com/product/P{s}{i}",
                "sku": f"SK-{s}{i}",
                "brand": "HomePro",
                "category": cat,
                "subcategory": sub,
                "description": "Solves an everyday problem, easy and quick to use. " * 6,
                "currency": "EUR",
                "price": 5.0 + i,
                "suggested_retail_price": 24.9 + i * 3,
                "proposed_retail_price": 24.9 + i * 3,
                "shipping_cost": 2.0,
                "shipping_min_days": 2,
                "shipping_max_days": 6,
                "stock_status": "In Stock",
                "stock_quantity": 50,
                "images": [f"https://cdn/{s}{i}-1.jpg", f"https://cdn/{s}{i}-2.jpg",
                           f"https://cdn/{s}{i}-3.jpg"],
                "variants": [],
                "active": True,
                "supplier": {
                    "id": f"S{s}", "name": f"EU Supplier {s}",
                    "url": f"https://app.syncee.com/supplier/S{s}",
                    "country": "Germany", "dispatch_countries": ["Germany"],
                    "ships_to_countries": ["Germany", "France", "Spain", "Italy", "Austria"],
                    "approval_required": False, "rating": 4.6, "review_count": 80,
                    "catalog_count": 200, "shipping_min_days": 2, "shipping_max_days": 6,
                    "shipping_policy_available": True, "return_policy_available": True,
                    "contact_available": True, "active": True,
                },
            })
    # One bad supplier: ships only outside target markets + slow -> gate failed.
    products.append({
        "id": "PBAD", "name": "Slow far gadget", "url": "https://app.syncee.com/product/PBAD",
        "sku": "BAD1", "category": "Home & Kitchen", "subcategory": "Gadgets",
        "currency": "EUR", "price": 3.0, "suggested_retail_price": 5.0, "shipping_cost": 4.0,
        "shipping_min_days": 20, "shipping_max_days": 40, "stock_status": "In Stock",
        "stock_quantity": 5, "images": ["https://cdn/bad.jpg"], "variants": [], "active": True,
        "supplier": {
            "id": "SBAD", "name": "FarAway", "url": "https://app.syncee.com/supplier/SBAD",
            "country": "China", "dispatch_countries": ["China"],
            "ships_to_countries": ["United States"], "approval_required": True, "rating": 3.0,
            "review_count": 5, "catalog_count": 9000, "shipping_min_days": 20,
            "shipping_max_days": 40, "shipping_policy_available": False,
            "return_policy_available": False, "contact_available": False, "active": True,
        },
    })
    return {"products": products}


class TestEndToEnd:
    def test_scan_score_select(self):
        c = cfg()
        p = InMemoryPersistence()

        scan = run_scan(c, source=FixtureSource(build_catalog()), persistence=p)
        assert scan.counts.products_created == 13  # 12 good + 1 bad
        assert scan.counts.suppliers_created == 4

        sup_summary = score_suppliers(p, c)
        assert sup_summary.approved == 3          # the 3 EU suppliers
        assert sup_summary.gate_failed == 1       # the far/slow one

        prod_summary = score_products(p, c)
        # Bad supplier's product must be excluded (spec §21).
        bad = p.products["pid:PBAD"]
        assert bad["Supplier Eligible"] is False
        assert bad["Review Status"] == "Excluded by Supplier"
        assert prod_summary.excluded_by_supplier == 1
        # Some good products should be shortlisted.
        assert prod_summary.shortlisted >= 6

        batch = make_initial_assortment(p, c)
        result = batch["result"]
        # Selection stays within configured bounds and excludes the bad product.
        assert result.count <= c.selection.initial_total_max
        assert "pid:PBAD" not in [cand.product_key for cand in result.selected]
        # Chosen products are marked candidates, not auto-selected (spec §26.6).
        for cand in result.selected:
            status = p.products[cand.product_key]["Selection Status"]
            assert status == "Initial Assortment Candidate"
        # A batch row was created.
        assert len(p.selection_batches) == 1

    def test_new_arrivals_batch(self):
        c = cfg()
        p = InMemoryPersistence()
        run_scan(c, source=FixtureSource(build_catalog()), persistence=p)
        score_suppliers(p, c)
        score_products(p, c)

        batch = make_new_arrivals(p, c)
        result = batch["result"]
        assert 1 <= result.count <= c.selection.new_arrivals_batch_size
        for cand in result.selected:
            assert p.products[cand.product_key]["Selection Status"] == "New Arrival Candidate"
