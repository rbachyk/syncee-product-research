"""Unit tests for the manual Baserow setup guide (spec §9)."""

from syncee_scanner.baserow.guide import render_setup_guide
from syncee_scanner.baserow.schemas import ALL_TABLES


def test_guide_covers_all_tables_and_primary_fields():
    guide = render_setup_guide()
    for table in ALL_TABLES:
        assert f"### Table: {table.name}" in guide
        assert f"Primary field: `{table.primary_field.name}`" in guide


def test_guide_lists_env_vars_and_token_only_message():
    guide = render_setup_guide()
    assert "BASEROW_DATABASE_TOKEN" in guide
    assert "only need a **database token**" in guide
    # Mentions select options and link targets so fields are unambiguous.
    assert "Single select" in guide
    assert "link to" in guide


def test_guide_includes_views():
    guide = render_setup_guide()
    assert "Approved Suppliers" in guide
    assert "Initial Assortment Candidates" in guide
