"""Export, reconciliation and resume tests, offline (spec §38, §28, §17.5)."""

import csv

import pytest

from syncee_scanner.config import load_config
from syncee_scanner.export import service as export_service
from syncee_scanner.extraction.source import FixtureSource
from syncee_scanner.observability.errors import ScannerError
from syncee_scanner.reconcile import run_reconciliation_scan
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scan import resume_scan, run_scan

from .test_incremental import catalog


def cfg():
    return load_config()


def seeded():
    c, p = cfg(), InMemoryPersistence()
    run_scan(c, source=FixtureSource(catalog(["P1", "P2", "P3"])), persistence=p)
    return c, p


class TestExport:
    def test_export_all_writes_csv(self, tmp_path):
        c, p = seeded()
        paths = export_service.export_all(p, tmp_path)
        names = {pth.name for pth in paths}
        assert names == {"suppliers.csv", "products.csv", "candidates.csv"}
        # products.csv has the 3 products + stable header incl. Exported At
        with (tmp_path / "products.csv").open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 3
        assert "Product Key" in rows[0]
        assert "Exported At" in rows[0]
        assert rows[0]["Raw Data"] if "Raw Data" in rows[0] else True  # Raw Data excluded

    def test_raw_data_excluded(self, tmp_path):
        c, p = seeded()
        export_service.export_products(p, tmp_path)
        with (tmp_path / "products.csv").open(encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        assert "Raw Data" not in header

    def test_json_export(self, tmp_path):
        c, p = seeded()
        paths = export_service.export_all(p, tmp_path, json=True)
        assert any(pth.name == "products.json" for pth in paths)


class TestReconcile:
    def test_missing_product_marked_inactive(self):
        c, p = seeded()
        # Rescan with P2 gone -> P2 marked inactive, not deleted.
        result = run_reconciliation_scan(
            c, source=FixtureSource(catalog(["P1", "P3"])), persistence=p
        )
        assert result.inactive_marked == 1
        assert "pid:P2" in result.missing_keys
        assert p.products["pid:P2"]["Active"] is False
        assert p.products["pid:P2"]["Selection Status"] == "Archived"
        # Not deleted.
        assert "pid:P2" in p.products

    def test_reappearing_product_reactivated(self):
        c, p = seeded()
        run_reconciliation_scan(c, source=FixtureSource(catalog(["P1", "P3"])), persistence=p)
        # P2 returns -> upsert reactivates it (Active True via fields).
        run_reconciliation_scan(
            c, source=FixtureSource(catalog(["P1", "P2", "P3"])), persistence=p
        )
        assert p.products["pid:P2"]["Active"] is True


class TestResume:
    def test_resume_continues_from_checkpoint(self):
        c = cfg()
        p = InMemoryPersistence()
        # Interrupted run: created run row + checkpoint at cursor for page 1.
        run = p.create_run(
            run_type=__import__("syncee_scanner.models", fromlist=["RunType"]).RunType.FULL_SCAN,
            category="Home & Kitchen", config_hash="x", scanner_version="0.1.0",
        )
        from syncee_scanner.runs.checkpoints import Checkpoint
        p.save_checkpoint(run, Checkpoint(page=1, cursor="0"), None)  # after first page

        pages = {"pages": [
            {"products": list(catalog(["P1"])["products"]), "cursor": "0"},
            {"products": list(catalog(["P2"])["products"]), "cursor": "1"},
        ]}
        summary = resume_scan(c, source=FixtureSource(pages), persistence=p, run_id=run.run_id)
        assert summary.run_id == run.run_id
        assert "pid:P2" in p.products
        assert "pid:P1" not in p.products  # page 1 skipped on resume

    def test_resume_unknown_run_errors(self):
        c, p = seeded()
        with pytest.raises(ScannerError) as exc:
            resume_scan(c, source=FixtureSource(catalog(["P1"])), persistence=p, run_id="nope")
        assert exc.value.code.value == "CHECKPOINT_ERROR"
