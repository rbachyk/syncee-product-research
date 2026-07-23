"""Margin calculation (spec §23).

Computes landed cost, gross margin amount and percentage from the proposed retail price
and supplier costs, plus configured fee/return allowances. When a required input is
missing the margin is explicitly ``Incomplete`` — the product must not receive a fully
validated margin score (spec §23.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..models import MarginStatus
from ..pricing import fx as fxmod


@dataclass
class MarginResult:
    status: MarginStatus
    landed_cost: float | None = None
    margin_amount: float | None = None
    margin_pct: float | None = None
    proposed_retail_price: float | None = None
    market_price: float | None = None  # Syncee suggested RRP, for competitiveness
    competitiveness: float | None = None  # RRP / proposed_retail (>=1 == at/below market)
    uncompetitive: bool = False
    shipping_estimated: bool = False  # True when shipping cost was assumed, not known


def _proposed_retail(product: dict) -> float | None:
    """Choose the retail price to margin against: proposed first, then suggested (spec §23)."""
    for key in ("proposed_retail_price", "suggested_retail_price"):
        value = product.get(key)
        if value is not None and value > 0:
            return float(value)
    return None


def compute_margin(
    product: dict, config: AppConfig, fx: fxmod.FxRates | None = None
) -> MarginResult:
    """Compute margin for a product (spec §23.1–§23.4).

    All monetary inputs are converted from the product's source ``currency`` to EUR first
    (via ``fx``, defaulting to the process-active rates) so cost, RRP and shipping share one
    basis — otherwise a foreign-currency price is wrongly treated as EUR (spec §23).

    Price and retail are required. When the shipping cost is unknown (Syncee's list API
    doesn't expose it) we optionally *estimate* it as a configured percentage of the
    supplier price so the product can still be scored/ranked, flagging
    ``shipping_estimated`` (spec §23.4 is relaxed via ``margin.estimate_shipping_when_unknown``).
    """
    m = config.margin
    fx = fx if fx is not None else fxmod.active()
    ccy = product.get("currency")

    def to_eur(value: float | None) -> float | None:
        if value is None or fx is None:
            return value
        converted = fx.convert(value, ccy)
        return converted if converted is not None else value  # unknown ccy → best effort

    supplier_price = to_eur(product.get("supplier_price"))
    shipping_cost = to_eur(product.get("shipping_cost"))
    retail = to_eur(_proposed_retail(product))

    # Without price or retail we can't estimate anything -> Incomplete (spec §23.4).
    if supplier_price is None or retail is None:
        return MarginResult(status=MarginStatus.INCOMPLETE, proposed_retail_price=retail)

    shipping_estimated = False
    if not product.get("shipping_cost_known") or shipping_cost is None:
        if not m.estimate_shipping_when_unknown:
            return MarginResult(status=MarginStatus.INCOMPLETE, proposed_retail_price=retail)
        shipping_cost = round(supplier_price * m.assumed_shipping_pct_of_price / 100, 2)
        shipping_estimated = True

    market_rrp = retail  # Syncee's suggested retail, kept for competitiveness
    fee_rate = (
        m.estimated_payment_fee_pct + m.estimated_platform_fee_pct
        + m.expected_return_allowance_pct
    ) / 100

    # Decide the retail price RB Home would actually sell at (spec §23; pricing_mode).
    if m.pricing_mode == "target_margin":
        denom = 1 - fee_rate - m.target_margin_pct / 100
        if denom <= 0:  # target unreachable given fees; fall back to RRP
            proposed = market_rrp
        else:
            proposed = (supplier_price + shipping_cost) / denom
    elif m.pricing_mode == "markup":
        proposed = supplier_price * m.markup_multiple
    else:  # "rrp"
        proposed = market_rrp
    if proposed is None or proposed <= 0:
        return MarginResult(status=MarginStatus.INCOMPLETE, market_price=market_rrp)

    fees = proposed * fee_rate
    landed = round(supplier_price + shipping_cost + fees, 2)
    margin_amount = round(proposed - landed, 2)
    margin_pct = round(margin_amount / proposed * 100, 1)

    competitiveness = round(market_rrp / proposed, 2) if market_rrp else None
    uncompetitive = bool(market_rrp) and proposed > m.uncompetitive_over_rrp * market_rrp

    if margin_pct < m.minimum_margin_pct:
        status = MarginStatus.BELOW_MINIMUM
    elif margin_pct >= m.target_margin_pct:
        status = MarginStatus.TARGET_MET
    else:
        status = MarginStatus.ACCEPTABLE

    return MarginResult(
        status=status,
        landed_cost=landed,
        margin_amount=margin_amount,
        margin_pct=margin_pct,
        proposed_retail_price=round(proposed, 2),
        market_price=market_rrp,
        competitiveness=competitiveness,
        uncompetitive=uncompetitive,
        shipping_estimated=shipping_estimated,
    )
