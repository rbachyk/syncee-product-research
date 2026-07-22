"""Tests for the declarative Syncee mapper + SynceeSource (spec §5.4, §8.4)."""

import pytest

from syncee_scanner.config import load_config
from syncee_scanner.extraction.mapper import (
    SynceeMapping,
    SynceeResponseMapper,
    get_path,
    load_mapping,
)
from syncee_scanner.extraction.records import normalize_product, normalize_supplier
from syncee_scanner.extraction.source import SynceeSource
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scan import run_scan


def api_product(pid, cursor_supplier="S1"):
    """A product shaped like a plausible Syncee GraphQL response node."""
    return {
        "id": pid,
        "title": f"Product {pid}",
        "url": f"https://app.syncee.com/marketplace/product/{pid}",
        "sku": f"SKU-{pid}",
        "brand": "HomePro",
        "category": "Home & Kitchen",
        "subcategory": "Kitchen",
        "description": "Solves kitchen clutter, easy to use.",
        "currency": "EUR",
        "price": 5.5,
        "rrp": 19.9,
        "shipping": {"cost": 2.0, "minDays": 2, "maxDays": 6},
        "stock": {"status": "In Stock", "quantity": 40},
        "images": [{"url": "https://cdn/1.jpg"}, {"url": "https://cdn/2.jpg"}],
        "mainImage": "https://cdn/1.jpg",
        "variants": [],
        "shipsFrom": "Germany",
        "createdAt": "2026-07-01T00:00:00Z",
        "updatedAt": "2026-07-10T00:00:00Z",
        "active": True,
        "supplier": {
            "id": cursor_supplier, "name": "EU Supplier", "url": "https://x/s/1",
            "country": "Germany", "dispatchCountries": ["Germany"],
            "shipsToCountries": ["Germany", "France", "Spain"], "approvalRequired": False,
            "rating": 4.6, "reviewCount": 80, "productCount": 200,
            "shipping": {"minDays": 2, "maxDays": 6},
            "hasShippingPolicy": True, "hasReturnPolicy": True, "hasContact": True,
            "active": True,
        },
    }


def api_response(pids, end_cursor, has_next):
    return {
        "data": {
            "products": [api_product(p) for p in pids],
            "pageInfo": {"endCursor": end_cursor, "hasNextPage": has_next},
        }
    }


def mapping_with_image_field() -> SynceeMapping:
    m = SynceeMapping()
    m.product.images_item_field = "url"  # images are [{url: ...}]
    return m


class TestGetPath:
    def test_nested_and_index(self):
        obj = {"a": {"b": [{"c": 7}]}}
        assert get_path(obj, "a.b.0.c") == 7

    def test_missing_returns_default(self):
        assert get_path({"a": 1}, "a.b.c", default="x") == "x"
        assert get_path(None, "a") is None


class TestMapper:
    def test_maps_product_and_supplier(self):
        mapper = SynceeResponseMapper(mapping_with_image_field())
        page = mapper.map_response(api_response(["P1", "P2"], "cur2", True))
        assert page.raw_count == 2
        assert page.next_cursor == "cur2"
        assert page.has_next is True
        p = page.products[0]
        assert p["id"] == "P1"
        assert p["price"] == 5.5
        assert p["images"] == ["https://cdn/1.jpg", "https://cdn/2.jpg"]  # item field applied
        assert p["supplier"]["ships_to_countries"] == ["Germany", "France", "Spain"]

    def test_mapped_product_normalizes_cleanly(self):
        mapper = SynceeResponseMapper(mapping_with_image_field())
        raw = mapper.map_product(api_product("P9"))
        ns = normalize_supplier(raw["supplier"])
        np = normalize_product(raw, supplier_key_value=ns["supplier_key"])
        assert np["product_key"] == "pid:P9"
        assert ns["supplier_key"] == "sid:S1"
        assert np["supplier_price"] == 5.5

    def test_bad_products_path_warns(self):
        m = SynceeMapping()
        m.list.products_path = "data.nope"
        page = SynceeResponseMapper(m).map_response({"data": {}})
        assert page.products == []


class TestLoadMapping:
    def test_loads_default_yaml(self):
        m = load_mapping()  # config/syncee_mapping.yaml exists in repo (confirmed via discovery)
        assert m.list.products_path == "result"
        assert m.list.method == "POST"
        assert m.product.name == "TITLE"

    def test_missing_file_uses_defaults(self, tmp_path):
        m = load_mapping(tmp_path / "nope.yaml")
        assert m.product.name == "title"


class TestSynceeSource:
    def test_paginates_via_cursor(self):
        pages = {
            None: api_response(["P1", "P2"], "cur1", True),
            "cur1": api_response(["P3"], "cur2", False),
        }
        calls = []

        def transport(cursor):
            calls.append(cursor)
            return pages[cursor]

        source = SynceeSource(
            transport=transport, mapper=SynceeResponseMapper(mapping_with_image_field())
        )
        got = list(source.iter_pages())
        assert calls == [None, "cur1"]
        assert [len(p.products) for p in got] == [2, 1]
        assert got[-1].has_next is False

    def test_no_transport_raises(self):
        with pytest.raises(NotImplementedError):
            list(SynceeSource().iter_pages())

    def test_end_to_end_scan_via_syncee_source(self):
        pages = {
            None: api_response(["P1", "P2"], "cur1", True),
            "cur1": api_response(["P3"], None, False),
        }
        source = SynceeSource(
            transport=lambda c: pages[c],
            mapper=SynceeResponseMapper(mapping_with_image_field()),
        )
        p = InMemoryPersistence()
        summary = run_scan(load_config(), source=source, persistence=p)
        assert summary.counts.products_created == 3
        assert "pid:P1" in p.products


def _syncee_shape(ids, total):
    """A response shaped like the real syncee-product-service search API."""
    return {
        "took": 5, "total": total,
        "result": [
            {
                "ID": pid, "USER_ID": "S1", "TITLE": f"Product {pid}",
                "IMAGES": ["https://cdn/a.jpg"],
                "VARIANTS": [{"PRICE": 5.5, "RETAIL_PRICE": 19.9, "QTY": 10}],
                "SUPPLIER": {"companyName": "EU Supplier", "paymentCurrency": "EUR"},
                "SETTINGS": {"warehouseLocation": "Ireland", "approveNeeded": False},
            }
            for pid in ids
        ],
    }


def _offset_mapping() -> SynceeMapping:
    """A mapping matching config/syncee_mapping.yaml's offset-POST shape."""
    from syncee_scanner.extraction.mapper import load_mapping

    return load_mapping()  # repo config is the confirmed offset mapping


class TestOffsetPagination:
    def test_offset_increments_and_stops_at_total(self):
        calls = []

        def transport(payload):
            calls.append((payload["from"], payload["size"]))
            start = payload["from"]
            ids = [f"P{i}" for i in range(start, min(start + payload["size"], 5))]
            return _syncee_shape(ids, total=5)

        mapping = _offset_mapping()
        mapping.list.categories = []  # single-category behavior
        mapping.list.page_size = 2
        source = SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping))
        pages = list(source.iter_pages())
        # 5 items, size 2 -> offsets 0,2,4 then stop (4+2 >= 5)
        assert [c[0] for c in calls] == [0, 2, 4]
        assert [len(p.products) for p in pages] == [2, 2, 1]
        assert pages[-1].has_next is False
        # request_template fields are forwarded (category etc.)
        assert calls  # sanity

    def test_offset_forwards_request_template(self):
        seen = {}

        def transport(payload):
            seen.update(payload)
            return _syncee_shape([], total=0)

        mapping = _offset_mapping()
        mapping.list.categories = []  # single-category: uses request_template.category
        source = SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping))
        list(source.iter_pages())
        assert seen.get("category") == 205
        assert seen.get("countryCode") == "IE"
        assert seen.get("from") == 0

    def test_offset_resume_from_cursor(self):
        def transport(payload):
            start = payload["from"]
            ids = [f"P{i}" for i in range(start, min(start + payload["size"], 5))]
            return _syncee_shape(ids, total=5)

        mapping = _offset_mapping()
        mapping.list.categories = []
        mapping.list.page_size = 2
        source = SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping))
        pages = list(source.iter_pages(start_cursor="4"))  # resume at offset 4
        assert [p.products[0]["id"] for p in pages] == ["P4"]

    def test_scans_each_category_in_turn(self):
        calls = []

        def transport(payload):
            calls.append((payload["category"], payload["from"]))
            # each category has exactly 1 page of 1 product
            return _syncee_shape([f"C{payload['category']}"], total=1)

        mapping = _offset_mapping()
        mapping.list.categories = [979, 1209, 977]  # explicit, not config-dependent
        mapping.list.page_size = 100
        source = SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping))
        pages = list(source.iter_pages())
        assert [c[0] for c in calls] == [979, 1209, 977]
        assert [p.products[0]["id"] for p in pages] == ["C979", "C1209", "C977"]
        assert pages[-1].has_next is False
        # cursor encodes category index + offset
        assert pages[0].cursor == "0:100"

    def test_resume_into_second_category(self):
        def transport(payload):
            return _syncee_shape([f"C{payload['category']}"], total=1)

        mapping = _offset_mapping()
        mapping.list.categories = [979, 1209, 977]
        source = SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping))
        # resume at category index 2 (third category = 977)
        pages = list(source.iter_pages(start_cursor="2:0"))
        assert [p.products[0]["id"] for p in pages] == ["C977"]

    def test_detail_response_enriches(self):
        """The same mapper turns a real product-detail response into full data (spec §8.4)."""
        import json
        from pathlib import Path

        from syncee_scanner.extraction.records import normalize_product, normalize_supplier
        from syncee_scanner.scoring.margin import compute_margin

        detail = json.loads(
            (Path(__file__).parent.parent / "fixtures" / "syncee_product_detail.json").read_text()
        )
        mapper = SynceeResponseMapper(_offset_mapping())
        raw = mapper.map_product(detail)
        ns = normalize_supplier(raw["supplier"])
        np = normalize_product(raw, supplier_key_value=ns["supplier_key"])
        assert np["product_key"] == "pid:1540862_89947_7377516691511"
        assert np["brand"] == "CAN"
        assert np["syncee_category"] == "Kitchen Tools & Utensils"
        # Prefer DEFAULT_CURRENCY_PRICE (retailer currency) over supplier-currency PRICE.
        assert np["supplier_price"] == 71.4286592 and np["suggested_retail_price"] == 127.99
        # Real shipping present -> known (not estimated)
        assert np["shipping_cost"] == 0.0 and np["shipping_cost_known"] is True
        assert np["shipping_max_days"] == 14
        assert np["stock_quantity"] == 200
        assert "<p>" not in (np["description"] or "")  # HTML stripped
        assert ns["contact_information_available"] is True
        assert "Germany" in ns["ships_to_countries"]
        # Real margin (not estimated) — thin here, correctly below minimum.
        m = compute_margin(np, load_config())
        assert m.shipping_estimated is False
        assert m.margin_pct is not None

    def test_real_shape_normalizes(self):
        mapping = _offset_mapping()
        mapper = SynceeResponseMapper(mapping)
        page = mapper.map_response(_syncee_shape(["X1"], total=1))
        assert page.total == 1
        p = page.products[0]
        assert p["id"] == "X1"
        assert p["price"] == 5.5
        assert p["suggested_retail_price"] == 19.9
        assert p["supplier"]["id"] == "S1"
        assert p["supplier"]["country"] == "Ireland"
        # normalizes cleanly into keys
        ns = normalize_supplier(p["supplier"])
        np = normalize_product(p, supplier_key_value=ns["supplier_key"])
        assert np["product_key"] == "pid:X1"
        assert ns["supplier_key"] == "sid:S1"


def test_url_template_builds_product_url():
    from syncee_scanner.extraction.mapper import SynceeMapping, SynceeResponseMapper
    m = SynceeMapping()
    m.product.url_template = "https://syncee.test/p/{id}"
    raw = SynceeResponseMapper(m).map_product({"id": "P9", "title": "x"})
    assert raw["url"] == "https://syncee.test/p/P9"
