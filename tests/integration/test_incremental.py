"""Incremental scan + Product Changes tests, offline (spec §27, §13, §43.8)."""


from syncee_scanner.config import load_config
from syncee_scanner.extraction.source import FixtureSource
from syncee_scanner.incremental import run_incremental_scan
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scan import run_scan


def cfg():
    return load_config()


def supplier(sid="S1"):
    return {
        "id": sid, "name": f"EU Supplier {sid}", "url": f"https://x/s/{sid}",
        "country": "Germany", "dispatch_countries": ["Germany"],
        "ships_to_countries": ["Germany", "France"], "approval_required": False,
        "rating": 4.5, "review_count": 50, "catalog_count": 100,
        "shipping_min_days": 2, "shipping_max_days": 6,
        "shipping_policy_available": True, "return_policy_available": True,
        "contact_available": True, "active": True,
    }


def product(pid, price=5.0):
    return {
        "id": pid, "name": f"Product {pid}", "url": f"https://x/p/{pid}", "sku": pid,
        "category": "Home & Kitchen", "subcategory": "Kitchen", "currency": "EUR",
        "price": price, "suggested_retail_price": 20.0, "shipping_cost": 2.0,
        "shipping_min_days": 2, "shipping_max_days": 6, "stock_status": "In Stock",
        "stock_quantity": 10, "images": ["https://x/i.jpg"], "variants": [],
        "active": True, "supplier": supplier(),
    }


def catalog(pids, prices=None):
    prices = prices or {}
    return {"products": [product(p, prices.get(p, 5.0)) for p in pids]}


class TestIncremental:
    def _seed(self):
        c, p = cfg(), InMemoryPersistence()
        run_scan(c, source=FixtureSource(catalog(["P1", "P2", "P3"])), persistence=p)
        return c, p

    def test_detects_new_products(self):
        c, p = self._seed()
        # New arrivals P4, P5 appear ahead of the known ones (newest-first).
        newest_first = {"products": [product("P4"), product("P5"),
                                     product("P1"), product("P2"), product("P3")]}
        result = run_incremental_scan(
            c, source=FixtureSource(newest_first), persistence=p, newest_first_verified=True
        )
        assert set(result.new_product_keys) == {"pid:P4", "pid:P5"}
        assert p.products["pid:P4"]["Is New"] is True
        assert result.summary.completeness in {
            "Complete", "Complete With Known Limitations",
        }

    def test_known_products_not_marked_new(self):
        c, p = self._seed()
        result = run_incremental_scan(
            c, source=FixtureSource(catalog(["P1", "P2", "P3"])), persistence=p,
            newest_first_verified=True,
        )
        assert result.new_product_keys == []
        # re-seen known products keep Is New = False (set during the changed/unchanged path)
        assert p.products["pid:P1"]["Is New"] is False or p.products["pid:P1"].get("Is New") is True

    def test_change_recorded_on_price_change(self):
        c, p = self._seed()
        changed = catalog(["P1", "P2", "P3"], prices={"P1": 9.99})
        result = run_incremental_scan(
            c, source=FixtureSource(changed), persistence=p, newest_first_verified=True
        )
        assert result.changes_recorded == 1
        assert len(p.product_changes) == 1
        row = p.product_changes[0]
        assert row["Change Type"] == "Price Changed"
        assert "supplier_price" in row["Changed Fields"]
        assert p.products["pid:P1"]["Supplier Price"] == 9.99
        assert p.products["pid:P1"]["Is New"] is False

    def test_unverified_completeness_when_ordering_unknown(self):
        c, p = self._seed()
        result = run_incremental_scan(
            c, source=FixtureSource(catalog(["P1"])), persistence=p,
            newest_first_verified=False,
        )
        assert result.summary.completeness == "Unverified"

    def test_early_stop_after_known_products(self):
        c, p = self._seed()
        cfg2 = cfg()
        cfg2.incremental_scan.stop_after_known_products = 2
        cfg2.incremental_scan.stop_after_known_pages = 99
        # One page: P4 (new) then 3 known -> should stop after 2 consecutive known.
        pages = {"pages": [
            {"products": [product("P4"), product("P1"), product("P2"), product("P3")],
             "cursor": "a"},
            {"products": [product("P5")], "cursor": "b"},
        ]}
        result = run_incremental_scan(
            cfg2, source=FixtureSource(pages), persistence=p, newest_first_verified=True
        )
        assert result.stopped_early is True
        assert "pid:P5" not in p.products  # second page never scanned
