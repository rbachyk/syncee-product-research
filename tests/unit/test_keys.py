"""Unit tests for key generation (spec §10.1, §11.1, §41.1)."""

import pytest

from syncee_scanner.extraction import keys


class TestSupplierKey:
    def test_prefers_syncee_id(self):
        assert keys.supplier_key(syncee_supplier_id="42", supplier_url="https://x") == "sid:42"

    def test_falls_back_to_url(self):
        k = keys.supplier_key(supplier_url="https://Shop.com/s/1/?utm_source=a")
        assert k.startswith("surl:")
        # tracking params ignored -> same key
        assert k == keys.supplier_key(supplier_url="https://shop.com/s/1")

    def test_name_country_hash_last(self):
        k = keys.supplier_key(supplier_name="Acme Home", location_country="españa")
        assert k.startswith("shash:")
        # name alone is not unique from name+country of a different country
        other = keys.supplier_key(supplier_name="Acme Home", location_country="Germany")
        assert k != other

    def test_requires_something(self):
        with pytest.raises(ValueError):
            keys.supplier_key()


class TestProductKey:
    def test_prefers_product_id(self):
        k = keys.product_key(supplier_key="sid:1", syncee_product_id="P9")
        assert k == "pid:P9"

    def test_sku_before_url(self):
        k = keys.product_key(supplier_key="sid:1", supplier_sku="SKU-1", product_url="https://x")
        assert k.startswith("psku:")
        # same sku + different supplier -> different key
        other = keys.product_key(supplier_key="sid:2", supplier_sku="SKU-1")
        assert k != other

    def test_url_fallback(self):
        k = keys.product_key(supplier_key="sid:1", product_url="https://s.com/p/5/")
        assert k.startswith("purl:")

    def test_name_variant_hash_last(self):
        a = keys.product_key(supplier_key="sid:1", product_name="Peeler", variants=[{"sku": "a"}])
        b = keys.product_key(supplier_key="sid:1", product_name="Peeler", variants=[{"sku": "b"}])
        assert a.startswith("phash:") and a != b  # variant signature differs

    def test_requires_supplier_key(self):
        with pytest.raises(ValueError):
            keys.product_key(supplier_key="", syncee_product_id="P1")

    def test_requires_something(self):
        with pytest.raises(ValueError):
            keys.product_key(supplier_key="sid:1")
