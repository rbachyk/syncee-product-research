"""Map normalized records to Baserow field payloads + UpsertRecords (spec §10, §11).

Turns the snake_case normalized dicts from :mod:`..extraction.records` into Baserow
field-name payloads and wraps them in :class:`~.repositories.UpsertRecord`s carrying the
create/changed/touch field splits (so First Seen At is never overwritten, spec §19.3).

New rows get the initial "Unscored / Unclassified / Not Selected" statuses; scoring,
classification and selection phases advance them later.
"""

from __future__ import annotations

import json
from typing import Any

from ..changes.fingerprints import product_fingerprint, supplier_fingerprint
from ..models import (
    Collection,
    HardGateStatus,
    ManualOverride,
    MarginStatus,
    ProductReviewStatus,
    SelectionStatus,
    SupplierEligibility,
)
from .repositories import UpsertRecord


def _join(values: list[str] | None, sep: str = ", ") -> str | None:
    if not values:
        return None
    return sep.join(values)


def _raw_json(raw: Any) -> str:
    return json.dumps(raw, ensure_ascii=False, default=str)


def supplier_upsert_record(
    normalized: dict[str, Any],
    *,
    now: str,
    relevant_product_count: int,
    scan_run_row_id: int | None,
    raw: Any,
) -> UpsertRecord:
    """Build an UpsertRecord for a supplier (spec §10.2)."""
    fields: dict[str, Any] = {
        "Supplier Key": normalized["supplier_key"],
        "Syncee Supplier ID": normalized.get("syncee_supplier_id"),
        "Supplier Name": normalized.get("supplier_name"),
        "Supplier URL": normalized.get("supplier_url"),
        "Location Country": normalized.get("location_country"),
        "Dispatch Countries": _join(normalized.get("dispatch_countries")),
        "Ships To Countries": _join(normalized.get("ships_to_countries")),
        "Approval Required": normalized.get("approval_required"),
        "Supplier Rating": normalized.get("supplier_rating"),
        "Review Count": normalized.get("review_count"),
        "Catalog Product Count": normalized.get("catalog_product_count"),
        "Relevant Product Count": relevant_product_count,
        "Shipping Min Days": normalized.get("shipping_min_days"),
        "Shipping Max Days": normalized.get("shipping_max_days"),
        "Shipping Policy Available": normalized.get("shipping_policy_available"),
        "Return Policy Available": normalized.get("return_policy_available"),
        "Contact Information Available": normalized.get("contact_information_available"),
        "Data Completeness %": normalized.get("data_completeness_pct"),
        "Last Seen At": now,
        "Active": bool(normalized.get("active", True)),
        "Raw Data": _raw_json(raw),
    }
    if scan_run_row_id is not None:
        fields["Last Scan Run"] = [scan_run_row_id]

    create_extra = {
        "First Seen At": now,
        "Hard Gate Status": HardGateStatus.UNSCORED.value,
        "Eligibility Status": SupplierEligibility.UNSCORED.value,
        "Manual Override": ManualOverride.NONE.value,
    }
    changed_extra = {"Last Changed At": now}
    touch = {"Last Seen At": now}
    if scan_run_row_id is not None:
        touch["Last Scan Run"] = [scan_run_row_id]

    return UpsertRecord(
        key=normalized["supplier_key"],
        fields=fields,
        fingerprint=supplier_fingerprint(normalized),
        create_extra=create_extra,
        changed_extra=changed_extra,
        touch_fields=touch,
    )


def product_upsert_record(
    normalized: dict[str, Any],
    *,
    now: str,
    supplier_row_id: int,
    scan_run_row_id: int | None,
    raw: Any,
) -> UpsertRecord:
    """Build an UpsertRecord for a product (spec §11.2).

    New products start Unscored/Unclassified/Not Selected with Supplier Eligible=False;
    the scoring phase advances them. ``Is New`` is True on create, cleared on later updates.
    """
    fields: dict[str, Any] = {
        "Product Key": normalized["product_key"],
        "Syncee Product ID": normalized.get("syncee_product_id"),
        "Product Name": normalized.get("product_name"),
        "Product URL": normalized.get("product_url"),
        "Supplier": [supplier_row_id],
        "Supplier SKU": normalized.get("supplier_sku"),
        "Brand": normalized.get("brand"),
        "Syncee Category": normalized.get("syncee_category"),
        "Syncee Subcategory": normalized.get("syncee_subcategory"),
        "Description": normalized.get("description"),
        "Currency": normalized.get("currency"),
        "Supplier Price": normalized.get("supplier_price"),
        "Suggested Retail Price": normalized.get("suggested_retail_price"),
        "Shipping Cost": normalized.get("shipping_cost"),
        "Shipping Cost Known": bool(normalized.get("shipping_cost_known")),
        "Stock Status": _stock_status(normalized.get("stock_status")),
        "Stock Quantity": normalized.get("stock_quantity"),
        "Variants Count": normalized.get("variants_count", 0),
        "Main Image URL": normalized.get("main_image_url"),
        "Image URLs": _join(normalized.get("image_urls"), sep="\n"),
        "Ships From": normalized.get("ships_from"),
        "Shipping Min Days": normalized.get("shipping_min_days"),
        "Shipping Max Days": normalized.get("shipping_max_days"),
        "Syncee Added At": normalized.get("syncee_added_at"),
        "Syncee Updated At": normalized.get("syncee_updated_at"),
        "Last Seen At": now,
        "Active": bool(normalized.get("active", True)),
        "Raw Data": _raw_json(raw),
    }
    if scan_run_row_id is not None:
        fields["Last Scan Run"] = [scan_run_row_id]

    create_extra = {
        "First Seen At": now,
        "Is New": True,
        "Supplier Eligible": False,
        "Product Gate Status": HardGateStatus.UNSCORED.value,
        "Margin Status": MarginStatus.UNKNOWN.value,
        "Collection": Collection.UNCLASSIFIED.value,
        "Review Status": ProductReviewStatus.UNSCORED.value,
        "Selection Status": SelectionStatus.NOT_SELECTED.value,
    }
    changed_extra = {"Last Changed At": now, "Is New": False}
    touch = {"Last Seen At": now}
    if scan_run_row_id is not None:
        touch["Last Scan Run"] = [scan_run_row_id]

    return UpsertRecord(
        key=normalized["product_key"],
        fields=fields,
        fingerprint=product_fingerprint(normalized),
        create_extra=create_extra,
        changed_extra=changed_extra,
        touch_fields=touch,
    )


_STOCK_VALUES = {"in stock": "In Stock", "out of stock": "Out Of Stock", "low stock": "Low Stock"}


def _stock_status(value: str | None) -> str:
    if not value:
        return "Unknown"
    return _STOCK_VALUES.get(value.lower(), "Unknown")
