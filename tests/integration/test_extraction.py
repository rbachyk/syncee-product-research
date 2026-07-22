"""Integration tests for extraction + normalization + source pagination (spec §41.2)."""

from pathlib import Path

import pytest

from syncee_scanner.extraction.pagination import PaginationGuard
from syncee_scanner.extraction.records import normalize_product, normalize_supplier
from syncee_scanner.extraction.source import FixtureSource
from syncee_scanner.observability.errors import PaginationLoopError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "home_kitchen_products.json"


class TestNormalizeSupplier:
    def test_maps_and_computes_completeness(self):
        raw = {
            "id": "S10", "name": " KitchenPro Supplies ", "url": "https://x/s/10/",
            "country": "Deutschland", "dispatch_countries": ["Germany"],
            "ships_to_countries": ["Germany", "France"], "rating": "4.7",
            "shipping_min_days": 3, "shipping_max_days": 7,
            "shipping_policy_available": "yes", "return_policy_available": "no",
            "contact_available": True,
        }
        s = normalize_supplier(raw)
        assert s["supplier_key"] == "sid:S10"
        assert s["supplier_name"] == "KitchenPro Supplies"
        assert s["location_country"] == "Germany"
        assert s["supplier_rating"] == 4.7
        assert s["return_policy_available"] is False
        assert 0 < s["data_completeness_pct"] <= 100


class TestNormalizeProduct:
    def test_maps_price_and_images(self):
        raw = {
            "id": "P100", "name": "Garlic Press", "url": "https://x/p/100",
            "sku": "GP", "price": "4,50", "shipping_cost": "2,00",
            "images": ["https://x/a.jpg", "https://x/a.jpg"], "variants": [{"sku": "A"}],
        }
        p = normalize_product(raw, supplier_key_value="sid:S10")
        assert p["product_key"] == "pid:P100"
        assert p["supplier_price"] == 4.5
        assert p["shipping_cost"] == 2.0
        assert p["shipping_cost_known"] is True
        assert p["variants_count"] == 1
        assert p["main_image_url"] == "https://x/a.jpg"

    def test_missing_shipping_cost_flagged_unknown(self):
        p = normalize_product({"id": "P1", "name": "x", "price": 1.0}, supplier_key_value="sid:S")
        assert p["shipping_cost_known"] is False
        assert p["shipping_cost"] is None


class TestFixtureSource:
    def test_iterates_pages(self):
        src = FixtureSource.from_file(FIXTURE)
        pages = list(src.iter_pages())
        assert [p.page_number for p in pages] == [1, 2]
        assert len(pages[0].products) == 2
        assert pages[0].has_next is True
        assert pages[1].has_next is False

    def test_resume_from_cursor(self):
        src = FixtureSource.from_file(FIXTURE)
        pages = list(src.iter_pages(start_cursor="p1"))  # resume after page 1
        assert [p.page_number for p in pages] == [2]


class TestPaginationGuard:
    def test_detects_repeated_cursor(self):
        g = PaginationGuard(max_pages=100)
        g.check(page_number=1, cursor="a")
        with pytest.raises(PaginationLoopError):
            g.check(page_number=2, cursor="a")

    def test_safety_limit(self):
        g = PaginationGuard(max_pages=1)
        g.check(page_number=1, cursor="a")
        with pytest.raises(PaginationLoopError):
            g.check(page_number=2, cursor="b")
