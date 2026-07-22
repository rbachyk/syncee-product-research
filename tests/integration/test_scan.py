"""End-to-end scan orchestration tests, fully offline (spec §17, §41.2, §43.4)."""

from pathlib import Path

from syncee_scanner.config import load_config
from syncee_scanner.extraction.source import FixtureSource
from syncee_scanner.models import RunType
from syncee_scanner.runs.manager import RunHandle
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scan import run_scan

FIXTURE = Path(__file__).parent.parent / "fixtures" / "home_kitchen_products.json"


def cfg():
    return load_config()


class TestFullScan:
    def test_scan_ingests_products_and_suppliers(self):
        p = InMemoryPersistence()
        summary = run_scan(cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p)
        assert summary.status == "Completed"
        assert summary.completeness == "Complete"
        # 3 products, 3 suppliers in the fixture
        assert summary.counts.products_created == 3
        assert summary.counts.suppliers_created == 3
        assert summary.counts.new_products == 3
        assert summary.supplier_count == 3
        # products link to supplier row ids
        prod = p.products["pid:P100"]
        assert prod["Supplier"] == [p.suppliers["sid:S10"]["id"]]
        # new products get initial statuses
        assert prod["Review Status"] == "Unscored"
        assert prod["Is New"] is True

    def test_idempotent_rerun_no_duplicates(self):
        p = InMemoryPersistence()
        run_scan(cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p)
        summary2 = run_scan(cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p)
        # second run: nothing created, all unchanged (spec §16.5, §43.3)
        assert summary2.counts.products_created == 0
        assert summary2.counts.products_unchanged == 3
        assert summary2.counts.suppliers_created == 0
        assert len(p.products) == 3
        assert len(p.suppliers) == 3

    def test_change_is_detected_on_rerun(self):
        p = InMemoryPersistence()
        run_scan(cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p)

        # Mutate a product price and rescan.
        import json
        data = json.loads(FIXTURE.read_text())
        data["pages"][0]["products"][0]["price"] = "9,99"
        summary = run_scan(cfg(), source=FixtureSource(data), persistence=p)
        assert summary.counts.products_updated == 1
        assert summary.counts.products_created == 0
        assert p.products["pid:P100"]["Supplier Price"] == 9.99
        assert p.products["pid:P100"]["Is New"] is False

    def test_limit_marks_partial(self):
        p = InMemoryPersistence()
        summary = run_scan(
            cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p, limit=2
        )
        assert summary.completeness == "Partial"
        assert summary.counts.products_created == 2

    def test_first_seen_preserved_on_update(self):
        p = InMemoryPersistence()
        run_scan(cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p)
        first_seen = p.products["pid:P100"]["First Seen At"]

        import json
        data = json.loads(FIXTURE.read_text())
        data["pages"][0]["products"][0]["price"] = "7,77"
        run_scan(cfg(), source=FixtureSource(data), persistence=p)
        assert p.products["pid:P100"]["First Seen At"] == first_seen  # not overwritten


class TestResume:
    def test_resume_from_checkpoint_cursor(self):
        p = InMemoryPersistence()
        # Simulate an interrupted run that stopped after page 1 (cursor "p1").
        run = RunHandle(run_id="full-scan-test", row_id=99)
        summary = run_scan(
            cfg(), source=FixtureSource.from_file(FIXTURE), persistence=p,
            run_type=RunType.FULL_SCAN, start_cursor="p1", resume_run=run,
        )
        # Only page 2's single product should be ingested on resume.
        assert summary.counts.products_created == 1
        assert "pid:P102" in p.products
        assert "pid:P100" not in p.products
