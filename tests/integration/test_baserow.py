"""Integration tests for the Baserow layer with mocked HTTP (spec §16, §41.2).

No live Baserow access — respx intercepts httpx requests.
"""

import httpx
import pytest
import respx

from syncee_scanner.baserow.client import BaserowClient
from syncee_scanner.baserow.indexes import KeyIndex
from syncee_scanner.baserow.repositories import UpsertRecord, upsert_records
from syncee_scanner.baserow.schemas import SUPPLIERS
from syncee_scanner.baserow.validation import validate_table
from syncee_scanner.observability.errors import BaserowAuthError, BaserowSchemaMismatch

API = "https://baserow.test"


def make_client(**kw) -> BaserowClient:
    return BaserowClient(API, "tok", retry_backoff_seconds=0, **kw)


class TestClient:
    def test_requires_token(self):
        with pytest.raises(BaserowAuthError):
            BaserowClient(API, "")

    @respx.mock
    def test_field_map(self):
        respx.get(f"{API}/api/database/fields/table/5/").mock(
            return_value=httpx.Response(200, json=[
                {"id": 1, "name": "Supplier Key"},
                {"id": 2, "name": "Supplier Name"},
            ])
        )
        client = make_client()
        fm = client.field_map(5)
        assert fm["Supplier Key"]["id"] == 1

    @respx.mock
    def test_iter_rows_paginates(self):
        route = respx.get(f"{API}/api/database/rows/table/9/")
        route.side_effect = [
            httpx.Response(200, json={"results": [{"id": 1}], "next": "x"}),
            httpx.Response(200, json={"results": [{"id": 2}], "next": None}),
        ]
        client = make_client()
        rows = list(client.iter_rows(9))
        assert [r["id"] for r in rows] == [1, 2]

    @respx.mock
    def test_auth_error_not_retried(self):
        respx.get(f"{API}/api/database/fields/table/5/").mock(
            return_value=httpx.Response(401, json={})
        )
        with pytest.raises(BaserowAuthError):
            make_client().list_fields(5)

    @respx.mock
    def test_transient_500_then_success(self):
        route = respx.get(f"{API}/api/database/fields/table/5/")
        route.side_effect = [
            httpx.Response(503, json={}),
            httpx.Response(200, json=[{"id": 1, "name": "Supplier Key"}]),
        ]
        assert make_client().list_fields(5)[0]["name"] == "Supplier Key"


class TestValidation:
    @respx.mock
    def test_missing_required_field_raises(self):
        respx.get(f"{API}/api/database/fields/table/5/").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "name": "Supplier Key"}])
        )
        with pytest.raises(BaserowSchemaMismatch):
            validate_table(make_client(), 5, SUPPLIERS)


class TestUpsert:
    @respx.mock
    def test_classifies_new_changed_unchanged(self):
        create = respx.post(f"{API}/api/database/rows/table/7/batch/").mock(
            return_value=httpx.Response(
                200, json={"items": [{"id": 100, "Record Fingerprint": "fpN"}]}
            )
        )
        update = respx.patch(f"{API}/api/database/rows/table/7/batch/").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        index = KeyIndex()
        index.add("sid:changed", 10, "old")
        index.add("sid:same", 11, "fpS")

        records = [
            UpsertRecord("sid:new", {"Supplier Key": "sid:new"}, "fpN"),
            UpsertRecord("sid:changed", {"Supplier Key": "sid:changed"}, "fpC"),
            UpsertRecord(
                "sid:same", {"Supplier Key": "sid:same"}, "fpS",
                touch_fields={"Last Seen At": "2026-07-19T00:00:00+00:00"},
            ),
        ]
        result = upsert_records(
            make_client(), 7, key_field="Supplier Key",
            fingerprint_field="Record Fingerprint",
            records=records, index=index, batch_size=100,
        )
        assert (result.created, result.updated, result.unchanged) == (1, 1, 1)
        assert result.key_to_row_id["sid:new"] == 100
        assert result.key_to_row_id["sid:changed"] == 10
        assert create.called and update.called
        # new key is now in the index with its fingerprint (idempotency for next page)
        assert index.get("sid:new").fingerprint == "fpN"

    @respx.mock
    def test_idempotent_second_run_no_writes(self):
        create = respx.post(f"{API}/api/database/rows/table/7/batch/")
        update = respx.patch(f"{API}/api/database/rows/table/7/batch/")
        index = KeyIndex()
        index.add("sid:same", 11, "fpS")
        records = [UpsertRecord("sid:same", {"Supplier Key": "sid:same"}, "fpS")]
        result = upsert_records(
            make_client(), 7, key_field="Supplier Key",
            fingerprint_field="Record Fingerprint",
            records=records, index=index, batch_size=100,
        )
        assert (result.created, result.updated, result.unchanged) == (0, 0, 1)
        assert not create.called and not update.called

    def test_dry_run_writes_nothing(self):
        index = KeyIndex()
        records = [UpsertRecord("sid:new", {"Supplier Key": "sid:new"}, "fpN")]
        result = upsert_records(
            make_client(), 7, key_field="Supplier Key",
            fingerprint_field="Record Fingerprint",
            records=records, index=index, batch_size=100, dry_run=True,
        )
        assert result.created == 1
        assert result.key_to_row_id["sid:new"] < 0  # placeholder id
