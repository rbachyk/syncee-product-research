"""Discovery report + CLI smoke tests (spec §8.3, §33, §41)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from syncee_scanner.cli import app
from syncee_scanner.discovery.report import DiscoveryFindings, write_discovery_artifacts

runner = CliRunner()
FIXTURE = Path(__file__).parent.parent / "fixtures" / "home_kitchen_products.json"

# All the Baserow env vars a developer's local .env might set.
_BASEROW_ENV = (
    "BASEROW_DATABASE_TOKEN", "BASEROW_SUPPLIERS_TABLE_ID", "BASEROW_PRODUCTS_TABLE_ID",
    "BASEROW_SCAN_RUNS_TABLE_ID", "BASEROW_PRODUCT_CHANGES_TABLE_ID",
    "BASEROW_MANUAL_DECISIONS_TABLE_ID", "BASEROW_SELECTION_BATCHES_TABLE_ID",
    "BASEROW_USER_EMAIL", "BASEROW_USER_PASSWORD", "BASEROW_WORKSPACE_ID",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Isolate CLI tests from any real .env so they behave as if Baserow is unconfigured."""
    monkeypatch.setattr("syncee_scanner.cli.load_dotenv", lambda *a, **k: None)
    for var in _BASEROW_ENV:
        monkeypatch.delenv(var, raising=False)


class TestDiscoveryReport:
    def test_writes_all_artifacts(self, tmp_path):
        findings = DiscoveryFindings(
            routes={"product_identity": "id", "supplier_identity": "id"},
            pagination={"strategy": "cursor", "extraction_method": "xhr"},
            network_endpoints=[{"endpoint": "https://x/api/products"}],
        )
        out = write_discovery_artifacts(findings, output_dir=tmp_path / "disc")
        for name in (
            "routes.json", "fields.json", "pagination.json", "sort_options.json",
            "network_endpoints.json", "sample_product_list_response.json",
            "discovery_report.md",
        ):
            assert (out / name).exists()
        assert (out / "screenshots").is_dir()
        assert findings.gate_ready() is True
        assert "Discovery Gate ready:** YES" in (out / "discovery_report.md").read_text()

    def test_gate_not_ready_without_identity(self):
        assert DiscoveryFindings().gate_ready() is False


class TestCli:
    def test_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.stdout

    def test_scan_full_offline_fixture(self):
        result = runner.invoke(
            app,
            ["scan", "full", "--fixture", str(FIXTURE), "--limit", "50", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Scan summary" in result.stdout
        assert "products_seen: 3" in result.stdout

    def test_classify_points_to_score(self):
        result = runner.invoke(app, ["classify", "products"])
        assert result.exit_code == 0
        assert "score products" in result.stdout

    def test_export_requires_baserow(self):
        result = runner.invoke(app, ["export", "all"])
        assert result.exit_code == 2

    def test_resume_requires_baserow(self):
        result = runner.invoke(app, ["scan", "resume", "some-run"])
        assert result.exit_code == 2

    def test_score_requires_baserow(self, monkeypatch):
        # With no Baserow env configured, scoring commands exit 2 with a clear message.
        for var in (
            "BASEROW_DATABASE_TOKEN", "BASEROW_SUPPLIERS_TABLE_ID",
            "BASEROW_PRODUCTS_TABLE_ID", "BASEROW_SCAN_RUNS_TABLE_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        result = runner.invoke(app, ["score", "suppliers"])
        assert result.exit_code == 2
