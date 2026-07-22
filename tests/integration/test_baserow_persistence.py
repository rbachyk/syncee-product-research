"""Integration tests for the Baserow persistence backend (spec §16), mocked HTTP."""

import httpx
import respx

from syncee_scanner.baserow.client import BaserowClient
from syncee_scanner.baserow.persistence import BaserowPersistence
from syncee_scanner.baserow.schemas import (
    T_PRODUCT_CHANGES,
    T_PRODUCTS,
    T_SCAN_RUNS,
    T_SELECTION_BATCHES,
    T_SUPPLIERS,
)

API = "https://baserow.test"
TABLE_IDS = {
    T_SUPPLIERS: 1, T_PRODUCTS: 2, T_SCAN_RUNS: 3,
    T_PRODUCT_CHANGES: 4, T_SELECTION_BATCHES: 6,
}


def make(dry_run=False) -> BaserowPersistence:
    client = BaserowClient(API, "tok", retry_backoff_seconds=0)
    return BaserowPersistence(client, TABLE_IDS, dry_run=dry_run)


class TestReviewOps:
    @respx.mock
    def test_iter_products_flattens_selects_and_links(self):
        # Baserow returns single-selects as {id,value,color} and links as [{id,value}].
        respx.get(f"{API}/api/database/rows/table/2/").mock(
            return_value=httpx.Response(200, json={"results": [{
                "id": 1, "Product Key": "pid:1",
                "Review Status": {"id": 9, "value": "Shortlisted", "color": "green"},
                "Supplier": [{"id": 55, "value": "sid:1"}],
                "Product Score": 82.0,
            }], "next": None})
        )
        row = make().iter_products()[0]
        assert row["Review Status"] == "Shortlisted"   # flattened to value string
        assert row["Supplier"] == [55]                  # flattened to [row_id]
        assert row["Product Score"] == 82.0             # plain field untouched

    @respx.mock
    def test_update_product(self):
        route = respx.patch(f"{API}/api/database/rows/table/2/batch/").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        make().update_product(9, {"Product Score": 80})
        assert route.called
        sent = route.calls[0].request
        assert b'"id":9' in sent.content

    @respx.mock
    def test_create_product_change(self):
        route = respx.post(f"{API}/api/database/rows/table/4/batch/").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 55}]})
        )
        rid = make().create_product_change({"Change ID": "c1"})
        assert rid == 55 and route.called

    @respx.mock
    def test_create_selection_batch_links_products(self):
        route = respx.post(f"{API}/api/database/rows/table/6/batch/").mock(
            return_value=httpx.Response(200, json={"items": [{"id": 70}]})
        )
        rid = make().create_selection_batch({"Batch ID": "b1"}, [11, 12])
        assert rid == 70
        assert b'"Products":[11,12]' in route.calls[0].request.content

    @respx.mock
    def test_load_run_parses_checkpoint(self):
        respx.get(f"{API}/api/database/rows/table/3/").mock(
            return_value=httpx.Response(200, json={
                "results": [
                    {"id": 8, "Run ID": "run-x",
                     "Checkpoint Data": '{"page": 5, "cursor": "c5"}'},
                ],
                "next": None,
            })
        )
        loaded = make().load_run("run-x")
        assert loaded is not None
        run, cp = loaded
        assert run.row_id == 8 and cp.page == 5 and cp.cursor == "c5"

    @respx.mock
    def test_load_run_missing_returns_none(self):
        respx.get(f"{API}/api/database/rows/table/3/").mock(
            return_value=httpx.Response(200, json={"results": [], "next": None})
        )
        assert make().load_run("nope") is None


class TestDryRun:
    def test_dry_run_skips_writes(self):
        # No respx routes registered -> any HTTP call would error; dry-run must not call.
        p = make(dry_run=True)
        p.update_product(1, {"x": 1})
        p.update_supplier(1, {"x": 1})
        assert p.create_product_change({"Change ID": "c"}) is None
        assert p.create_selection_batch({"Batch ID": "b"}, [1]) is None
