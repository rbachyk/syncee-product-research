"""Product hard gates + risk detection (spec §22, §22.1).

Hard gates (1–8) are pass/fail. High-risk product types (spec §22.1) are surfaced as risk
flags that route a product to manual review rather than silently passing — a high-risk
product must never pass automatically (spec §43.6). Margin validation is handled in
:mod:`.product_score` so gates stay purely structural.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import AppConfig
from ..models import HardGateStatus
from .reason_codes import ProductReason

# High-risk keyword -> flag (spec §22.1). Matched against title + description.
RISK_KEYWORDS: dict[str, str] = {
    "HEATING": r"\b(heater|heating|electric blanket|hot plate|kettle|toaster|iron)\b",
    "ELECTRICAL": r"\b(plug|adapter|charger|led|wired|voltage|220v|110v|electric)\b",
    "BATTERY": r"\b(battery|batteries|rechargeable|li-ion|lithium)\b",
    "FOOD_CONTACT": r"\b(food[- ]?grade|food contact|drinking|bpa|silicone mold|straw)\b",
    "MEDICAL": r"\b(medical|therap|cure|treatment|posture correct|anti[- ]?bacterial)\b",
    "FRAGILE_GLASS": r"\b(glass|crystal|ceramic|porcelain)\b",
    "TRADEMARK": (
        r"\b(disney|marvel|nike|adidas|apple|lego|gucci|super\s?mario|nintendo|"
        r"spider[- ]?man|batman|superman|pok[eé]mon|hello\s?kitty|harry\s?potter|"
        r"star\s?wars|minecraft|barbie|frozen elsa|paw\s?patrol|peppa\s?pig)\b"
    ),
    "COMPLEX_SIZING": r"\b(size chart|us size|eu size|fits|clothing|apparel|shoe)\b",
}

_COMPLIANCE_RISKS = {"HEATING", "ELECTRICAL", "BATTERY", "FOOD_CONTACT", "MEDICAL", "TRADEMARK"}
_REFUND_RISKS = {"FRAGILE_GLASS", "COMPLEX_SIZING"}


@dataclass
class ProductGateResult:
    status: HardGateStatus
    reasons: list[ProductReason] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == HardGateStatus.PASSED


def detect_risk_flags(product: dict) -> list[str]:
    """Detect high-risk product-type flags from title + description (spec §22.1)."""
    text = f"{product.get('product_name') or ''} {product.get('description') or ''}".lower()
    return [flag for flag, pattern in RISK_KEYWORDS.items() if re.search(pattern, text)]


def _scope_text(product: dict) -> str:
    """Title + description + the supplier's own category labels (spec §22 gate 2).

    The category/subcategory is often the only reliable signal: a scented candle can be
    titled "Just Married!" with no product-type word, yet carry category "Kerzen".
    """
    parts = (
        product.get("product_name"),
        product.get("description"),
        product.get("syncee_category"),
        product.get("syncee_subcategory"),
    )
    return " ".join(p for p in parts if p).lower()


def is_excluded_type(product: dict, config: AppConfig) -> bool:
    """Whether the product matches a configured content-based scope exclusion.

    Consumables/food/skin products and refill/replacement-part-only listings are out of scope
    for RB Home regardless of score; matched against title + description + category labels.
    """
    text = _scope_text(product)
    return any(re.search(pat, text, re.IGNORECASE) for pat in config.product_gates.exclude_keywords)


def risk_reasons(flags: list[str]) -> list[ProductReason]:
    """Map risk flags to product reason codes."""
    reasons: list[ProductReason] = []
    if any(f in _COMPLIANCE_RISKS for f in flags):
        reasons.append(ProductReason.HIGH_COMPLIANCE_RISK)
    if any(f in _REFUND_RISKS for f in flags):
        reasons.append(ProductReason.HIGH_REFUND_RISK)
    return reasons


def evaluate_product_gates(
    product: dict, config: AppConfig, *, supplier_eligible: bool
) -> ProductGateResult:
    """Evaluate structural product hard gates 1–8 (spec §22)."""
    gates = config.product_gates
    reasons: list[ProductReason] = []

    if not supplier_eligible:
        reasons.append(ProductReason.SUPPLIER_REJECTED)
    if not product.get("active", True):
        reasons.append(ProductReason.INACTIVE)
    if not (product.get("product_name") or "").strip():
        reasons.append(ProductReason.NO_TITLE)
    if is_excluded_type(product, config):
        reasons.append(ProductReason.EXCLUDED_PRODUCT_TYPE)
    if gates.require_image and not (product.get("main_image_url") or product.get("image_urls")):
        reasons.append(ProductReason.INSUFFICIENT_IMAGES)
    if gates.require_known_supplier_price and product.get("supplier_price") is None:
        reasons.append(ProductReason.MISSING_PRICE)
    if gates.require_in_stock and not _in_stock(product):
        reasons.append(ProductReason.OUT_OF_STOCK)

    max_days = product.get("shipping_max_days")
    if max_days is not None and max_days > gates.max_shipping_days:
        reasons.append(ProductReason.SHIPPING_TOO_SLOW)

    status = HardGateStatus.PASSED if not reasons else HardGateStatus.FAILED
    if not supplier_eligible:
        status = HardGateStatus.EXCLUDED_BY_SUPPLIER

    flags = detect_risk_flags(product)
    return ProductGateResult(status=status, reasons=reasons, risk_flags=flags)


def _in_stock(product: dict) -> bool:
    status = (product.get("stock_status") or "").lower()
    if status in {"out of stock", "outofstock"}:
        return False
    qty = product.get("stock_quantity")
    if qty is not None:
        return qty > 0
    # Unknown stock status with no quantity: treat "in stock"/None as sellable.
    return status != "out of stock"
