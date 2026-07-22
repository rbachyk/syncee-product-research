"""Manual overrides with audit trail (spec §14, §20.8).

Every manual status change updates the supplier/product row *and* creates an immutable
Manual Decisions row recording previous/new status, decision, timestamp and actor
(spec §14.1). Supplier overrides re-derive eligibility via a rescore so the effect is
immediate and consistent with automated scoring (spec §20.8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ..config import AppConfig
from ..extraction.records import normalize_supplier
from ..models import (
    DecisionValue,
    EntityType,
    ManualOverride,
    ProductReviewStatus,
)
from ..observability.errors import ErrorCode, ScannerError
from .service import _raw, _supplier_fields
from .supplier_score import score_supplier


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _decision_id() -> str:
    return f"decision-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _find(rows: list[dict], key_field: str, key: str) -> dict:
    for row in rows:
        if row.get(key_field) == key:
            return row
    raise ScannerError(ErrorCode.CONFIGURATION_ERROR, f"{key_field} '{key}' not found")


def apply_supplier_override(
    persistence,
    config: AppConfig,
    supplier_key: str,
    override: ManualOverride,
    *,
    note: str | None = None,
    decided_by: str = "cli",
) -> str:
    """Apply Approve/Block/None to a supplier + audit + rescore (spec §20.8)."""
    row = _find(persistence.iter_suppliers(), "Supplier Key", supplier_key)
    previous = row.get("Eligibility Status", "")

    raw = _raw(row)
    norm = normalize_supplier(raw) if raw else {"supplier_key": supplier_key}
    norm["relevant_product_count"] = row.get("Relevant Product Count") or 0
    rescored = score_supplier(norm, config, manual_override=override.value)

    fields = _supplier_fields(rescored)
    fields["Manual Override"] = override.value
    if note:
        fields["Manual Notes"] = note
    persistence.update_supplier(row["id"], fields)

    decision = {
        ManualOverride.APPROVE: DecisionValue.APPROVE,
        ManualOverride.BLOCK: DecisionValue.BLOCK,
        ManualOverride.NONE: DecisionValue.RESTORE,
    }[override]
    persistence.create_manual_decision(
        {
            "Decision ID": _decision_id(),
            "Entity Type": EntityType.SUPPLIER.value,
            "Supplier": [row["id"]],
            "Previous Status": previous,
            "New Status": rescored.eligibility.value,
            "Decision": decision.value,
            "Reason": note or "",
            "Decided At": _now(),
            "Decided By": decided_by,
        }
    )
    return rescored.eligibility.value


def apply_product_decision(
    persistence,
    product_key: str,
    decision: DecisionValue,
    *,
    note: str | None = None,
    decided_by: str = "cli",
) -> str:
    """Approve or reject a product manually + audit, by Product Key (spec §14)."""
    row = _find(persistence.iter_products(), "Product Key", product_key)
    return decide_product(persistence, row, decision, note=note, decided_by=decided_by)


def decide_product(
    persistence,
    row: dict,
    decision: DecisionValue,
    *,
    note: str | None = None,
    decided_by: str = "cli",
) -> str:
    """Approve or reject an already-loaded product row + write the audit trail (spec §14).

    Shared by the CLI (``product approve/reject``) and the dashboard so both apply the exact
    same status change *and* immutable Manual Decisions record.
    """
    previous = row.get("Review Status", "")
    new_status = (
        ProductReviewStatus.APPROVED
        if decision == DecisionValue.APPROVE
        else ProductReviewStatus.MANUALLY_REJECTED
    )
    fields = {"Review Status": new_status.value}
    if note:
        fields["Manual Notes"] = note
    persistence.update_product(row["id"], fields)

    persistence.create_manual_decision(
        {
            "Decision ID": _decision_id(),
            "Entity Type": EntityType.PRODUCT.value,
            "Product": [row["id"]],
            "Previous Status": previous,
            "New Status": new_status.value,
            "Decision": decision.value,
            "Reason": note or "",
            "Decided At": _now(),
            "Decided By": decided_by,
        }
    )
    return new_status.value
