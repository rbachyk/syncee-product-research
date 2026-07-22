"""Change detection and classification (spec §13, §19.3).

Given a previously-stored record and a freshly-normalized one, determine which tracked
fields changed and classify the change into a spec §13 change type. The output feeds a
Product Changes row and drives ``Last Seen At`` / ``Last Changed At`` updates (spec §19.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .fingerprints import TRACKED_PRODUCT_FIELDS, product_fingerprint


class ChangeType(str, Enum):
    """Product Changes change types (spec §13)."""

    PRICE_CHANGED = "Price Changed"
    SHIPPING_CHANGED = "Shipping Changed"
    STOCK_CHANGED = "Stock Changed"
    CONTENT_CHANGED = "Content Changed"
    SUPPLIER_CHANGED = "Supplier Changed"
    AVAILABILITY_CHANGED = "Availability Changed"
    MULTIPLE_CHANGES = "Multiple Changes"


# Map each tracked field to the change category it belongs to.
_FIELD_CATEGORY: dict[str, ChangeType] = {
    "supplier_price": ChangeType.PRICE_CHANGED,
    "suggested_retail_price": ChangeType.PRICE_CHANGED,
    "shipping_cost": ChangeType.SHIPPING_CHANGED,
    "shipping_min_days": ChangeType.SHIPPING_CHANGED,
    "shipping_max_days": ChangeType.SHIPPING_CHANGED,
    "stock_quantity": ChangeType.STOCK_CHANGED,
    "stock_status": ChangeType.AVAILABILITY_CHANGED,
    "active": ChangeType.AVAILABILITY_CHANGED,
    "supplier_key": ChangeType.SUPPLIER_CHANGED,
    "product_name": ChangeType.CONTENT_CHANGED,
    "description": ChangeType.CONTENT_CHANGED,
    "main_image_url": ChangeType.CONTENT_CHANGED,
    "image_urls": ChangeType.CONTENT_CHANGED,
    "variants_count": ChangeType.CONTENT_CHANGED,
    "syncee_category": ChangeType.CONTENT_CHANGED,
    "syncee_subcategory": ChangeType.CONTENT_CHANGED,
}


@dataclass
class ChangeResult:
    """Outcome of comparing a stored record with a freshly observed one."""

    changed: bool
    change_type: ChangeType | None = None
    changed_fields: list[str] = field(default_factory=list)
    previous_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)
    new_fingerprint: str = ""


def classify_change(changed_fields: list[str]) -> ChangeType | None:
    """Reduce a set of changed fields to one change type (Multiple if mixed)."""
    if not changed_fields:
        return None
    categories = {_FIELD_CATEGORY.get(f, ChangeType.CONTENT_CHANGED) for f in changed_fields}
    if len(categories) == 1:
        return next(iter(categories))
    return ChangeType.MULTIPLE_CHANGES


def detect_product_change(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    previous_fingerprint: str | None = None,
) -> ChangeResult:
    """Compare a stored product with a freshly-normalized one (spec §19.3).

    Args:
        previous: the last-stored normalized record, or None for a brand-new product.
        current: the freshly-normalized record.
        previous_fingerprint: stored fingerprint; if given, used as a fast-path guard.

    Returns:
        ChangeResult. ``changed`` is False when nothing tracked differs (only Last Seen
        At should move). For a new product (``previous is None``) returns changed=False —
        creation is handled by the upsert path, not the change log.
    """
    new_fp = product_fingerprint(current)
    if previous is None:
        return ChangeResult(changed=False, new_fingerprint=new_fp)

    old_fp = previous_fingerprint or product_fingerprint(previous)
    if old_fp == new_fp:
        return ChangeResult(changed=False, new_fingerprint=new_fp)

    changed_fields: list[str] = []
    prev_vals: dict[str, Any] = {}
    new_vals: dict[str, Any] = {}
    for f in TRACKED_PRODUCT_FIELDS:
        if previous.get(f) != current.get(f):
            changed_fields.append(f)
            prev_vals[f] = previous.get(f)
            new_vals[f] = current.get(f)

    return ChangeResult(
        changed=bool(changed_fields),
        change_type=classify_change(changed_fields),
        changed_fields=changed_fields,
        previous_values=prev_vals,
        new_values=new_vals,
        new_fingerprint=new_fp,
    )
