"""Unit tests for Baserow setup field-payload translation (spec §9, §16.2)."""

import pytest

from syncee_scanner.baserow.schemas import FieldDef, FieldType
from syncee_scanner.baserow.setup import _field_payload
from syncee_scanner.observability.errors import BaserowError


class TestFieldPayload:
    def test_number_with_decimals(self):
        p = _field_payload(FieldDef("Price", FieldType.NUMBER, number_decimals=2), {})
        assert p == {"name": "Price", "type": "number", "number_decimal_places": 2,
                     "number_negative": True}

    def test_date_includes_time(self):
        p = _field_payload(FieldDef("Seen", FieldType.DATE), {})
        assert p["type"] == "date" and p["date_include_time"] is True

    def test_single_select_builds_options_with_colors(self):
        p = _field_payload(
            FieldDef("Status", FieldType.SINGLE_SELECT, select_options=["A", "B", "C"]), {}
        )
        assert p["type"] == "single_select"
        assert [o["value"] for o in p["select_options"]] == ["A", "B", "C"]
        assert all("color" in o for o in p["select_options"])

    def test_link_row_resolves_target(self):
        p = _field_payload(
            FieldDef("Supplier", FieldType.LINK_ROW, link_table="Suppliers"),
            {"Suppliers": 77},
        )
        assert p == {"name": "Supplier", "type": "link_row", "link_row_table_id": 77}

    def test_link_row_missing_target_raises(self):
        with pytest.raises(BaserowError):
            _field_payload(FieldDef("S", FieldType.LINK_ROW, link_table="Suppliers"), {})

    def test_plain_types(self):
        for ftype, name in [
            (FieldType.TEXT, "text"), (FieldType.LONG_TEXT, "long_text"),
            (FieldType.URL, "url"), (FieldType.BOOLEAN, "boolean"),
        ]:
            p = _field_payload(FieldDef("F", ftype), {})
            assert p == {"name": "F", "type": name}
