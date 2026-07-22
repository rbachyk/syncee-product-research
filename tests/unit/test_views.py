"""Unit tests for Baserow view specs + filter payloads (spec §30)."""

import pytest

from syncee_scanner.baserow.schemas import (
    PRODUCTS,
    SCAN_RUNS,
    SUPPLIERS,
    FieldType,
    TableDef,
)
from syncee_scanner.baserow.views import (
    VIEW_SPECS,
    FilterSpec,
    ViewSpec,
    build_filter_payloads,
)
from syncee_scanner.observability.errors import BaserowError

TABLES = {t.name: t for t in (SUPPLIERS, PRODUCTS, SCAN_RUNS)}


def fake_field_map(table: TableDef) -> dict[str, dict]:
    """Build a synthetic Baserow field map (name -> metadata) from the schema."""
    fm = {}
    for i, f in enumerate(table.fields, start=1):
        meta = {"id": i * 10, "name": f.name}
        if f.type == FieldType.SINGLE_SELECT:
            meta["select_options"] = [
                {"id": i * 100 + j, "value": v} for j, v in enumerate(f.select_options)
            ]
        fm[f.name] = meta
    return fm


class TestBuildPayloads:
    def test_single_select_resolves_option_id(self):
        fm = fake_field_map(SUPPLIERS)
        spec = ViewSpec("x", SUPPLIERS.name,
                        (FilterSpec("Eligibility Status", "single_select_equal", "Approved"),))
        payloads = build_filter_payloads(spec, fm)
        assert len(payloads) == 1
        assert payloads[0]["type"] == "single_select_equal"
        assert payloads[0]["value"].isdigit()

    def test_boolean(self):
        fm = fake_field_map(PRODUCTS)
        spec = ViewSpec("x", PRODUCTS.name, (FilterSpec("Active", "boolean", False),))
        assert build_filter_payloads(spec, fm)[0]["value"] == "0"

    def test_empty_and_not_empty(self):
        fm = fake_field_map(PRODUCTS)
        spec = ViewSpec("x", PRODUCTS.name, (FilterSpec("Risk Flags", "not_empty"),))
        assert build_filter_payloads(spec, fm)[0]["value"] == ""

    def test_unknown_field_raises(self):
        spec = ViewSpec("x", SUPPLIERS.name, (FilterSpec("Nope", "boolean", True),))
        with pytest.raises(BaserowError):
            build_filter_payloads(spec, fake_field_map(SUPPLIERS))

    def test_unknown_option_raises(self):
        spec = ViewSpec("x", SUPPLIERS.name,
                        (FilterSpec("Eligibility Status", "single_select_equal", "Bogus"),))
        with pytest.raises(BaserowError):
            build_filter_payloads(spec, fake_field_map(SUPPLIERS))


class TestAllSpecsValidAgainstSchema:
    def test_every_view_spec_resolves(self):
        # Catches typos in field names / select option values across all VIEW_SPECS.
        for spec in VIEW_SPECS:
            table = TABLES[spec.table]
            payloads = build_filter_payloads(spec, fake_field_map(table))
            assert len(payloads) == len(spec.filters)

    def test_covers_all_three_tables(self):
        tables = {s.table for s in VIEW_SPECS}
        assert tables == set(TABLES)
