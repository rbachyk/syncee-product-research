"""Offline test of the enrich + initial-assortment funnel (spec §5.4, §26)."""

from syncee_scanner.config import load_config
from syncee_scanner.enrich import enrich_products
from syncee_scanner.extraction.mapper import SynceeResponseMapper, load_mapping
from syncee_scanner.extraction.source import SynceeSource
from syncee_scanner.pipeline import run_initial_pipeline
from syncee_scanner.runs.persistence import InMemoryPersistence


def cfg():
    c = load_config()
    c.classification.category_collection_map = {}  # content-based classification
    return c


def list_product(pid, price, retail):
    """A sparse list-API product (no shipping/description)."""
    return {
        "ID": pid, "USER_ID": f"S{pid}", "TITLE": f"Kitchen gadget {pid} solves prep easily",
        "IMAGES": ["https://cdn/1.jpg", "https://cdn/2.jpg", "https://cdn/3.jpg"],
        "VARIANTS": [{"PRICE": price, "RETAIL_PRICE": retail}],
        "SUPPLIER": {"companyName": f"EU Supplier {pid}", "paymentCurrency": "EUR"},
        "SETTINGS": {"warehouseLocation": "Germany", "approveNeeded": False},
    }


def list_response(products, total):
    return {"took": 1, "total": total, "result": products}


def detail_for(pid, price, retail, ships_max=6):
    """A rich product-detail response with real shipping/description."""
    return {
        "ID": pid, "USER_ID": f"S{pid}", "TITLE": f"Kitchen gadget {pid} solves prep easily",
        "DESCRIPTION": "<p>Solves kitchen prep quickly and easily. Organize your countertop.</p>",
        "BRAND": "HomePro", "CATEGORY": "Kitchen Tools & Utensils",
        "IMAGES": ["https://cdn/1.jpg", "https://cdn/2.jpg", "https://cdn/3.jpg"],
        "VARIANTS": [{"PRICE": price, "RETAIL_PRICE": retail, "QTY": "150", "SKU": f"SK{pid}"}],
        "SETTINGS": {"warehouseLocation": "Germany", "approveNeeded": False},
        "SUPPLIER": {"companyName": f"EU Supplier {pid}", "paymentCurrency": "EUR",
                     "contactEmail": "a@b.com", "website": "https://x",
                     "shipsTo": ["Germany", "France", "Spain", "Italy", "Ireland"]},
        "SHIPPING": [{"shippingCost": 1.5, "minShippingDays": 2, "maxShippingDays": ships_max,
                      "locations": ["DE", "FR"]}],
        "CREATED": "2026-07-01 00:00:00", "LAST_MODIFIED": "2026-07-10 00:00:00",
    }


class FakeDetailTransport:
    def __init__(self, details):
        self.details = details
        self.calls = []

    def get_detail(self, pid):
        self.calls.append(pid)
        return self.details.get(pid)

    def close(self):
        pass


def _offline_mapping():
    m = load_mapping()
    m.list.categories = []          # single-category
    m.list.per_category_limit = 0
    m.list.page_size = 100
    return m


class TestEnrichAndFunnel:
    def test_pipeline_enriches_and_selects(self):
        c = cfg()
        p = InMemoryPersistence()

        # Two strong-margin products + one thin-margin, all as sparse list data.
        pids = {
            "P1": (5.0, 20.0),   # 75% gross before fees -> strong
            "P2": (6.0, 24.0),
            "P3": (18.0, 20.0),  # thin margin -> should be rejected after enrich
        }
        pages = [list_response([list_product(k, *v) for k, v in pids.items()], total=3)]
        mapping = _offline_mapping()

        def list_transport(payload):
            return pages[payload["from"] // payload["size"]] if payload["from"] == 0 else \
                list_response([], total=3)

        details = {k: detail_for(k, *v) for k, v in pids.items()}

        result = run_initial_pipeline(
            c, p,
            make_source=lambda: SynceeSource(
                transport=list_transport, mapper=SynceeResponseMapper(mapping)
            ),
            make_transport=lambda: FakeDetailTransport(details),
            scan_limit=None, enrich_top=3,
        )
        # All three scanned and enriched.
        assert result.enrich.enriched == 3
        # Enriched products carry real detail data now.
        prod = p.products["pid:P1"]
        assert prod["Shipping Cost"] == 1.5
        assert prod["Stock Quantity"] == 150
        assert prod["Description"]
        assert prod["Collection"] == "Kitchen Convenience"
        # Competitive product shortlists and outranks the uncompetitive (thin) one, which
        # carries the UNCOMPETITIVE_PRICE flag (its target-margin price is well above RRP).
        assert p.products["pid:P1"]["Review Status"] == "Shortlisted"
        p1_score = p.products["pid:P1"]["Product Score"] or 0
        p3_score = p.products["pid:P3"]["Product Score"] or 0
        assert p1_score > p3_score
        assert "UNCOMPETITIVE_PRICE" in (p.products["pid:P3"]["Exclusion Reason Codes"] or "")
        # A selection batch was produced.
        assert result.batch["result"].count >= 1

    def test_enrich_top_limits_calls(self):
        c = cfg()
        p = InMemoryPersistence()
        # Seed 5 products via a scan, give them different pre-scores by price spread.
        prods = [list_product(f"P{i}", 5.0 + i, 25.0) for i in range(5)]
        mapping = _offline_mapping()
        run_scan_products(c, p, prods, mapping)
        # pre-rank
        from syncee_scanner.scoring.service import score_products, score_suppliers
        score_suppliers(p, c)
        score_products(p, c)

        details = {f"P{i}": detail_for(f"P{i}", 5.0 + i, 25.0) for i in range(5)}
        result = enrich_products(
            p, FakeDetailTransport(details), c, SynceeResponseMapper(mapping), top=2
        )
        assert result.enriched == 2  # only top-2 enriched

    def test_enrich_all_in_resumable_chunks(self):
        c = cfg()
        p = InMemoryPersistence()
        # 5 products, no top cap: enrich them all, two at a time, skipping done ones.
        prods = [list_product(f"P{i}", 5.0, 25.0) for i in range(5)]
        mapping = _offline_mapping()
        run_scan_products(c, p, prods, mapping)
        details = {f"P{i}": detail_for(f"P{i}", 5.0, 25.0) for i in range(5)}

        def chunk():
            return enrich_products(
                p, FakeDetailTransport(details), c, SynceeResponseMapper(mapping),
                limit=2, skip_enriched=True,
            )

        assert chunk().enriched == 2          # first chunk
        assert chunk().enriched == 2          # second chunk (skips the first 2)
        assert chunk().enriched == 1          # last one
        assert chunk().enriched == 0          # nothing left → resumable "done" signal
        assert all(p.products[f"pid:P{i}"].get("Enriched At") for i in range(5))


def run_scan_products(c, p, syncee_products, mapping):
    from syncee_scanner.scan import run_scan

    def transport(payload):
        return list_response(syncee_products if payload["from"] == 0 else [],
                             total=len(syncee_products))

    run_scan(c, source=SynceeSource(transport=transport, mapper=SynceeResponseMapper(mapping)),
             persistence=p)


class TestEnrichSupplierSpread:
    def test_per_supplier_cap_spreads_enrichment(self):
        c = cfg()
        p = InMemoryPersistence()
        # 6 products: 4 from supplier A, 2 from supplier B, all pre-scored.
        prods = ([list_product(f"A{i}", 5.0, 25.0) for i in range(4)]
                 + [list_product(f"B{i}", 5.0, 25.0) for i in range(2)])
        # force same supplier per group by overriding USER_ID
        for pr in prods[:4]:
            pr["USER_ID"] = "SA"
            pr["SUPPLIER"]["companyName"] = "Supplier A"
        for pr in prods[4:]:
            pr["USER_ID"] = "SB"
            pr["SUPPLIER"]["companyName"] = "Supplier B"
        mapping = _offline_mapping()
        run_scan_products(c, p, prods, mapping)
        from syncee_scanner.scoring.service import score_products
        score_products(p, c)

        details = {**{f"A{i}": detail_for(f"A{i}", 5.0, 25.0) for i in range(4)},
                   **{f"B{i}": detail_for(f"B{i}", 5.0, 25.0) for i in range(2)}}
        result = enrich_products(
            p, FakeDetailTransport(details), c, SynceeResponseMapper(mapping),
            top=10, per_supplier_cap=2,
        )
        # capped at 2 per supplier -> 4 enriched (2 A + 2 B), covering both suppliers
        assert result.enriched == 4
        assert result.suppliers_updated == 2
