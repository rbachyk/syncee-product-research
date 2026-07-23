"""Normalize raw source records into canonical dicts (spec §18, §19).

Source adapters (fixture or live Syncee) emit a *canonical raw* schema — documented below —
and these functions turn that into normalized dicts keyed by the snake_case names used for
key generation (:mod:`.keys`), fingerprints (:mod:`..changes.fingerprints`) and Baserow
field mapping (:mod:`.mapping`). Keeping this seam narrow means the only Syncee-specific
code is the adapter that produces the canonical raw schema (informed by discovery, §8.4).

Canonical raw supplier keys: id, name, url, country, dispatch_countries, ships_to_countries,
approval_required, rating, review_count, catalog_count, shipping_min_days, shipping_max_days,
shipping_policy_available, return_policy_available, contact_available, active.

Canonical raw product keys: id, name, url, sku, brand, category, subcategory, description,
currency, price, suggested_retail_price, shipping_cost, shipping_min_days, shipping_max_days,
stock_status, stock_quantity, images (list), main_image, variants (list), ships_from,
added_at, updated_at, active, supplier (nested canonical raw supplier).
"""

from __future__ import annotations

from typing import Any

from . import normalization as nz
from .keys import product_key, supplier_key, variant_signature

# Supplier fields contributing to Data Completeness % (spec §10.2 "Data Completeness %").
_SUPPLIER_COMPLETENESS_FIELDS = (
    "supplier_name",
    "supplier_url",
    "location_country",
    "dispatch_countries",
    "ships_to_countries",
    "shipping_min_days",
    "shipping_max_days",
    "shipping_policy_available",
    "return_policy_available",
    "contact_information_available",
)


def _data_completeness(record: dict[str, Any], fields: tuple[str, ...]) -> float:
    present = sum(1 for f in fields if _is_present(record.get(f)))
    return round(100.0 * present / len(fields), 1)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, str)) and len(value) == 0:
        return False
    return True


def normalize_supplier(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a canonical raw supplier into a normalized dict with a Supplier Key."""
    name = nz.normalize_text(raw.get("name"))
    url = nz.normalize_url(raw.get("url"))
    country = nz.normalize_country(raw.get("country"))
    key = supplier_key(
        syncee_supplier_id=raw.get("id"),
        supplier_url=raw.get("url"),
        supplier_name=raw.get("name"),
        location_country=raw.get("country"),
    )
    record: dict[str, Any] = {
        "supplier_key": key,
        "syncee_supplier_id": nz.normalize_text(str(raw["id"])) if raw.get("id") else None,
        "supplier_name": name,
        "supplier_url": url,
        "location_country": country,
        "dispatch_countries": nz.normalize_country_list(raw.get("dispatch_countries")),
        "ships_to_countries": nz.normalize_country_list(raw.get("ships_to_countries")),
        "approval_required": nz.normalize_bool(raw.get("approval_required")),
        "supplier_rating": nz.normalize_price(raw.get("rating")),
        "review_count": _as_int(raw.get("review_count")),
        "catalog_product_count": _as_int(raw.get("catalog_count")),
        "shipping_min_days": _as_int(raw.get("shipping_min_days")),
        "shipping_max_days": _as_int(raw.get("shipping_max_days")),
        "shipping_policy_available": nz.normalize_bool(raw.get("shipping_policy_available")),
        "return_policy_available": nz.normalize_bool(raw.get("return_policy_available")),
        # A contact email/website string counts as "contact available"; also accept a bool.
        "contact_information_available": _presence_bool(raw.get("contact_available")),
        "active": nz.normalize_bool(raw.get("active")) if raw.get("active") is not None else True,
    }
    if raw.get("manual_override"):  # single-source platforms can pre-approve themselves
        record["manual_override"] = raw["manual_override"]
    record["data_completeness_pct"] = _data_completeness(
        record, _SUPPLIER_COMPLETENESS_FIELDS
    )
    return record


def _target_market_shipping(zones: list, target_codes: list[str]) -> dict | None:
    """Worst-case shipping across the target markets, from Syncee's per-country zones.

    Each zone has ``shippingCost``/``minShippingDays``/``maxShippingDays`` and a ``locations``
    list of ISO codes. For each target market we take its covering zone, then aggregate:
    cost = max (most expensive market), days = max (slowest), and count how many markets ship.
    Conservative by design — if a product is profitable/fast enough to the worst target
    market, it works for all of them.
    """
    if not zones or not target_codes:
        return None
    covered: dict[str, tuple] = {}
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        locs = set(zone.get("locations") or [])
        for code in target_codes:
            if code in locs and code not in covered:
                covered[code] = (
                    zone.get("shippingCost"),
                    zone.get("minShippingDays"),
                    zone.get("maxShippingDays"),
                )
    if not covered:
        return {"shipped": 0, "total": len(target_codes)}
    costs = [c[0] for c in covered.values() if c[0] is not None]
    mins = [c[1] for c in covered.values() if c[1] is not None]
    maxs = [c[2] for c in covered.values() if c[2] is not None]
    return {
        "cost": max(costs) if costs else None,          # worst-case (most expensive market)
        "min_days": min(mins) if mins else None,
        "max_days": max(maxs) if maxs else None,        # worst-case (slowest market)
        "shipped": len(covered),
        "total": len(target_codes),
    }


def normalize_product(
    raw: dict[str, Any], *, supplier_key_value: str, target_codes: list[str] | None = None
) -> dict[str, Any]:
    """Normalize a canonical raw product into a normalized dict with a Product Key."""
    name = nz.normalize_text(raw.get("name"))
    url = nz.normalize_url(raw.get("url"))
    images = [nz.normalize_url(u) for u in (raw.get("images") or []) if nz.normalize_url(u)]
    main_image = nz.normalize_url(raw.get("main_image")) or (images[0] if images else None)
    variants = raw.get("variants") or []
    key = product_key(
        supplier_key=supplier_key_value,
        syncee_product_id=raw.get("id"),
        supplier_sku=raw.get("sku"),
        product_url=raw.get("url"),
        product_name=raw.get("name"),
        variants=variants,
    )
    # Store the wholesale in the supplier's own currency (same basis as RRP and shipping);
    # margin converts all of them to EUR via daily FX (spec §23). `default_currency_price` is
    # an unreliable pre-conversion (its currency varies by supplier), so it's only a last resort.
    price = nz.normalize_price(raw.get("price")) or nz.normalize_price(
        raw.get("default_currency_price")
    )

    # Shipping: prefer worst-case across the target markets (per-country zones), else the
    # single fallback fields.
    tms = _target_market_shipping(raw.get("shipping_zones"), target_codes or [])
    if tms and "cost" in tms:
        shipping_cost = nz.normalize_price(tms.get("cost"))
        shipping_known = tms.get("cost") is not None
        shipping_min = _as_int(tms.get("min_days"))
        shipping_max = _as_int(tms.get("max_days"))
    else:
        shipping_cost = nz.normalize_price(raw.get("shipping_cost"))
        shipping_known = raw.get("shipping_cost") is not None
        shipping_min = _as_int(raw.get("shipping_min_days"))
        shipping_max = _as_int(raw.get("shipping_max_days"))
    markets_shipped = tms.get("shipped") if tms else None
    markets_total = tms.get("total") if tms else None

    return {
        "product_key": key,
        "syncee_product_id": nz.normalize_text(str(raw["id"])) if raw.get("id") else None,
        "product_name": name,
        "product_url": url,
        "supplier_key": supplier_key_value,
        "supplier_sku": nz.normalize_text(raw.get("sku")),
        "brand": nz.normalize_text(raw.get("brand")),
        "syncee_category": nz.normalize_text(raw.get("category")),
        "syncee_subcategory": nz.normalize_text(raw.get("subcategory")),
        "description": nz.strip_html(raw.get("description")),
        "currency": nz.normalize_text(raw.get("currency")),
        "supplier_price": price,
        "suggested_retail_price": nz.normalize_price(raw.get("suggested_retail_price")),
        "shipping_cost": shipping_cost,
        "shipping_cost_known": shipping_known,
        "shipping_min_days": shipping_min,
        "shipping_max_days": shipping_max,
        "target_markets_shipped": markets_shipped,
        "target_markets_total": markets_total,
        "stock_status": nz.normalize_text(raw.get("stock_status")),
        "stock_quantity": _as_int(raw.get("stock_quantity")),
        "variants_count": len(variants),
        "variant_signature": variant_signature(variants),
        "main_image_url": main_image,
        "image_urls": images,
        "ships_from": nz.normalize_text(raw.get("ships_from")),
        "syncee_added_at": nz.normalize_datetime(raw.get("added_at")),
        "syncee_updated_at": nz.normalize_datetime(raw.get("updated_at")),
        "active": nz.normalize_bool(raw.get("active")) if raw.get("active") is not None else True,
    }


def _presence_bool(value: Any) -> bool | None:
    """True if a bool True or a non-empty string/list (e.g. a contact email); None if absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
