"""Reason codes for scoring decisions (spec §20.7, §24.4).

Reason codes make every gate failure and rejection auditable (spec §43.10). They are
stored on the supplier/product row and echoed into logs. Kept as enums so the vocabulary
is fixed and typo-proof.
"""

from __future__ import annotations

from enum import Enum


class SupplierReason(str, Enum):
    """Supplier reason codes (spec §20.7)."""

    NO_TARGET_MARKET = "NO_TARGET_MARKET"
    DISPATCH_OUTSIDE_EUROPE = "DISPATCH_OUTSIDE_EUROPE"
    SHIPPING_TOO_SLOW = "SHIPPING_TOO_SLOW"
    SHIPPING_UNKNOWN = "SHIPPING_UNKNOWN"
    LOW_DATA_COMPLETENESS = "LOW_DATA_COMPLETENESS"
    NO_RETURN_POLICY = "NO_RETURN_POLICY"
    NO_SHIPPING_POLICY = "NO_SHIPPING_POLICY"
    LOW_SUPPLIER_SCORE = "LOW_SUPPLIER_SCORE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    MANUALLY_BLOCKED = "MANUALLY_BLOCKED"
    MANUALLY_APPROVED = "MANUALLY_APPROVED"
    INSUFFICIENT_RELEVANT_PRODUCTS = "INSUFFICIENT_RELEVANT_PRODUCTS"
    INACTIVE = "INACTIVE"


class ProductReason(str, Enum):
    """Product reason codes (spec §24.4)."""

    SUPPLIER_REJECTED = "SUPPLIER_REJECTED"
    OUTSIDE_STORE_SCOPE = "OUTSIDE_STORE_SCOPE"
    EXCLUDED_PRODUCT_TYPE = "EXCLUDED_PRODUCT_TYPE"
    LOW_MARGIN = "LOW_MARGIN"
    MARGIN_UNKNOWN = "MARGIN_UNKNOWN"
    MARGIN_ESTIMATED = "MARGIN_ESTIMATED"
    UNCOMPETITIVE_PRICE = "UNCOMPETITIVE_PRICE"
    SHIPPING_TOO_SLOW = "SHIPPING_TOO_SLOW"
    SHIPPING_COST_UNKNOWN = "SHIPPING_COST_UNKNOWN"
    HIGH_COMPLIANCE_RISK = "HIGH_COMPLIANCE_RISK"
    HIGH_REFUND_RISK = "HIGH_REFUND_RISK"
    LOW_CONTENT_POTENTIAL = "LOW_CONTENT_POTENTIAL"
    INSUFFICIENT_IMAGES = "INSUFFICIENT_IMAGES"
    MISSING_PRICE = "MISSING_PRICE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    DUPLICATE_CONCEPT = "DUPLICATE_CONCEPT"
    LOW_PRODUCT_SCORE = "LOW_PRODUCT_SCORE"
    INACTIVE = "INACTIVE"
    NO_TITLE = "NO_TITLE"


def encode(reasons) -> str:
    """Serialize a list of reason enums/strings to the stored comma-joined form."""
    return ", ".join(r.value if isinstance(r, Enum) else str(r) for r in reasons)
