"""Unit tests for fingerprints and change detection (spec §13, §19.3, §41.1)."""

from syncee_scanner.changes.detector import ChangeType, detect_product_change
from syncee_scanner.changes.fingerprints import product_fingerprint


def base_product() -> dict:
    return {
        "product_name": "Garlic Press",
        "description": "Presses garlic.",
        "supplier_price": 4.5,
        "suggested_retail_price": 12.0,
        "shipping_cost": 2.0,
        "shipping_min_days": 3,
        "shipping_max_days": 7,
        "stock_status": "In Stock",
        "stock_quantity": 100,
        "main_image_url": "https://img/1.jpg",
        "image_urls": ["https://img/1.jpg", "https://img/2.jpg"],
        "variants_count": 1,
        "supplier_key": "sid:1",
        "active": True,
        "syncee_category": "Kitchen",
        "syncee_subcategory": "Tools",
    }


class TestFingerprint:
    def test_stable_regardless_of_list_order(self):
        a = base_product()
        b = base_product()
        b["image_urls"] = list(reversed(b["image_urls"]))
        assert product_fingerprint(a) == product_fingerprint(b)

    def test_changes_when_price_changes(self):
        a = base_product()
        b = base_product()
        b["supplier_price"] = 5.0
        assert product_fingerprint(a) != product_fingerprint(b)


class TestDetect:
    def test_new_product_not_a_change(self):
        r = detect_product_change(None, base_product())
        assert r.changed is False
        assert r.new_fingerprint

    def test_unchanged(self):
        p = base_product()
        r = detect_product_change(p, dict(p))
        assert r.changed is False

    def test_price_change_classified(self):
        prev = base_product()
        cur = base_product()
        cur["supplier_price"] = 6.0
        r = detect_product_change(prev, cur)
        assert r.changed is True
        assert r.change_type == ChangeType.PRICE_CHANGED
        assert r.changed_fields == ["supplier_price"]
        assert r.previous_values["supplier_price"] == 4.5
        assert r.new_values["supplier_price"] == 6.0

    def test_multiple_changes(self):
        prev = base_product()
        cur = base_product()
        cur["supplier_price"] = 6.0
        cur["product_name"] = "Garlic Press Pro"
        r = detect_product_change(prev, cur)
        assert r.change_type == ChangeType.MULTIPLE_CHANGES
        assert set(r.changed_fields) == {"supplier_price", "product_name"}

    def test_availability_change(self):
        prev = base_product()
        cur = base_product()
        cur["active"] = False
        r = detect_product_change(prev, cur)
        assert r.change_type == ChangeType.AVAILABILITY_CHANGED
