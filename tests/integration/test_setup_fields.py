"""Integration test for creating fields in existing tables (spec §9), mocked HTTP."""

import httpx
import respx

from syncee_scanner.baserow.schemas import SUPPLIERS
from syncee_scanner.baserow.setup import BaserowSetup

API = "https://baserow.test"


def _setup() -> BaserowSetup:
    s = BaserowSetup(API)
    s._jwt = "jwt-token"  # skip authenticate()
    return s


@respx.mock
def test_creates_only_missing_fields_and_renames_primary():
    tid = 10
    # Existing table has a default primary "Name" and one real field already present.
    respx.get(f"{API}/api/database/fields/table/{tid}/").mock(
        return_value=httpx.Response(200, json=[
            {"id": 1, "name": "Name", "primary": True},
            {"id": 2, "name": "Supplier Name", "primary": False},
        ])
    )
    patched = respx.patch(f"{API}/api/database/fields/1/").mock(
        return_value=httpx.Response(200, json={"id": 1, "name": "Supplier Key"})
    )
    created = respx.post(f"{API}/api/database/fields/table/{tid}/").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )

    # Only the Suppliers table, pointed at tid; link target resolves to same map.
    table_ids = {t.name: (tid if t is SUPPLIERS else 20 + i)
                 for i, t in enumerate(__import__(
                     "syncee_scanner.baserow.schemas", fromlist=["ALL_TABLES"]).ALL_TABLES)}

    setup = _setup()
    # Patch ALL_TABLES down to just Suppliers for a focused assertion.
    import syncee_scanner.baserow.setup as setup_mod
    original = setup_mod.ALL_TABLES
    setup_mod.ALL_TABLES = [SUPPLIERS]
    try:
        summary = setup.create_fields_in_existing(table_ids)
    finally:
        setup_mod.ALL_TABLES = original
    setup.close()

    # Primary "Name" renamed to "Supplier Key"; "Supplier Name" skipped; rest created.
    assert patched.called
    body = patched.calls[0].request.content
    assert b"Supplier Key" in body
    s = summary["Suppliers"]
    assert s["created"] == len(SUPPLIERS.fields) - 2  # minus primary + the 1 existing field
    assert s["skipped"] == 2
    # A link field (Last Scan Run -> Scan Runs) was created with an integer target id.
    link_bodies = [c.request.content for c in created.calls if b"link_row" in c.request.content]
    assert link_bodies and b'"link_row_table_id"' in link_bodies[0]
