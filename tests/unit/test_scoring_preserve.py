"""Re-scoring must never overwrite a manual approve/reject decision (spec §14)."""

from syncee_scanner.config import load_config
from syncee_scanner.models import ProductReviewStatus, SupplierEligibility
from syncee_scanner.runs.persistence import InMemoryPersistence
from syncee_scanner.scoring.service import score_products


def test_manual_reject_preserved_on_rescore():
    p = InMemoryPersistence()
    p.suppliers["s1"] = {
        "id": 1, "Supplier Key": "s1",
        "Eligibility Status": SupplierEligibility.APPROVED.value, "Supplier Score": 80.0,
    }
    p.products["pid:rejected"] = {
        "id": 10, "Product Key": "pid:rejected", "Supplier": [1],
        "Review Status": ProductReviewStatus.MANUALLY_REJECTED.value,
        "Selection Status": "Not Selected", "Raw Data": "",
    }
    p.products["pid:normal"] = {
        "id": 11, "Product Key": "pid:normal", "Supplier": [1],
        "Review Status": ProductReviewStatus.UNSCORED.value, "Raw Data": "",
    }
    summary = score_products(p, load_config())
    assert summary.manual_preserved == 1
    assert p.products["pid:rejected"]["Review Status"] == "Manually Rejected"
    # the normal one was (re)scored — its status is no longer Unscored
    assert p.products["pid:normal"]["Review Status"] != ProductReviewStatus.UNSCORED.value
