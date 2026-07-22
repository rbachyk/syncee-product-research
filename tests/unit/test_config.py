"""Unit tests for configuration loading & validation (spec §32, §41.1)."""

import textwrap

import pytest

from syncee_scanner.config import BaserowCredentials, load_config
from syncee_scanner.observability.errors import ConfigurationError


class TestLoad:
    def test_defaults_load_and_validate(self):
        c = load_config()
        assert c.syncee.category == "Home & Kitchen"
        assert len(c.markets.target) == 9
        assert sum(c.supplier_scoring.weights.values()) == 100
        assert sum(c.product_scoring.weights.values()) == 100

    def test_config_hash_is_stable_and_sensitive(self, tmp_path):
        c1 = load_config()
        c2 = load_config()
        assert c1.config_hash() == c2.config_hash()

        override = tmp_path / "o.yaml"
        override.write_text("margin:\n  minimum_margin_pct: 50\n")
        c3 = load_config(override)
        assert c3.config_hash() != c1.config_hash()

    def test_missing_file_raises(self):
        with pytest.raises(ConfigurationError):
            load_config("nope.yaml")

    def test_bad_weights_raise(self, tmp_path):
        override = tmp_path / "bad.yaml"
        override.write_text(
            textwrap.dedent(
                """
                supplier_scoring:
                  weights:
                    market_coverage: 10
                """
            )
        )
        with pytest.raises(ConfigurationError):
            load_config(override)


class TestCredentials:
    def test_from_env(self):
        creds = BaserowCredentials.from_env(
            {"BASEROW_DATABASE_TOKEN": "t", "BASEROW_SUPPLIERS_TABLE_ID": "5"}
        )
        assert creds.database_token == "t"
        assert creds.suppliers_table_id == "5"

    def test_require_tables_raises_when_missing(self):
        with pytest.raises(ConfigurationError):
            BaserowCredentials.from_env({}).require_tables()
