"""Deterministic record fingerprints (spec §19.3).

A fingerprint is a stable hash over a record's *tracked normalized fields*. On repeated
observation the scanner compares fingerprints to decide whether ``Last Changed At`` moves
and whether a Product Changes row is created (spec §19.3). Field selection is explicit so
that noisy, non-meaningful values do not trigger spurious change events.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Product fields that make up the fingerprint (subset of spec §13 tracked fields).
# Source timestamps are tracked as metadata but intentionally excluded here so an
# updated_at bump alone does not register as a content change.
TRACKED_PRODUCT_FIELDS: tuple[str, ...] = (
    "product_name",
    "description",
    "supplier_price",
    "suggested_retail_price",
    "shipping_cost",
    "shipping_min_days",
    "shipping_max_days",
    "stock_status",
    "stock_quantity",
    "main_image_url",
    "image_urls",
    "variants_count",
    "supplier_key",
    "active",
    "syncee_category",
    "syncee_subcategory",
)

TRACKED_SUPPLIER_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "supplier_url",
    "location_country",
    "dispatch_countries",
    "ships_to_countries",
    "approval_required",
    "supplier_rating",
    "review_count",
    "shipping_min_days",
    "shipping_max_days",
    "shipping_policy_available",
    "return_policy_available",
    "active",
)


def _canonical(value: Any) -> Any:
    """Make a value order-stable and JSON-serializable for hashing."""
    if isinstance(value, (list, tuple)):
        return sorted(_canonical(v) for v in value)
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    return value


def compute_fingerprint(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    """Return a deterministic SHA-256 fingerprint over the given fields."""
    payload = {f: _canonical(record.get(f)) for f in fields}
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def product_fingerprint(record: dict[str, Any]) -> str:
    return compute_fingerprint(record, TRACKED_PRODUCT_FIELDS)


def supplier_fingerprint(record: dict[str, Any]) -> str:
    return compute_fingerprint(record, TRACKED_SUPPLIER_FIELDS)
