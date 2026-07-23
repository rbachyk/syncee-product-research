"""Declarative Syncee response → canonical raw mapping (spec §5.4, §8.4).

The live extraction seam is *data, not code*: discovery inspects Syncee's XHR/GraphQL
responses (spec §8.2) and records where each field lives; those dotted paths go into a
mapping config (``config/syncee_mapping.yaml``). :class:`SynceeResponseMapper` then turns a
raw API response into the canonical raw product schema consumed by
:mod:`.records` — no per-field code changes when Syncee's shape is confirmed.

This module is pure and fully unit-tested against a simulated response; the live transport
(Playwright network capture) is injected separately into :class:`~.source.SynceeSource`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

DEFAULT_MAPPING_PATH = Path("config/syncee_mapping.yaml")


def get_path(obj: Any, path: str | None, default: Any = None) -> Any:
    """Resolve a dotted path (e.g. ``supplier.address.country``) against nested dicts/lists.

    List indices are supported numerically (``images.0.url``). Returns ``default`` if any
    segment is missing.
    """
    if not path:
        return default
    current = obj
    for segment in path.split("."):
        if current is None:
            return default
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return default
        elif isinstance(current, dict):
            current = current.get(segment, None)
        else:
            return default
    return current if current is not None else default


class ListMapping(BaseModel):
    """Where the product array and pagination markers live in a list response.

    ``endpoint_template`` is the confirmed list URL; discovery fills it in. Two pagination
    modes are supported:
      * ``cursor`` (GET) — follow ``next_cursor_path`` / ``has_next_path``.
      * ``offset`` (POST) — send ``request_template`` with an incrementing ``from`` (by
        ``page_size``) and stop at ``total_path``.
    """

    products_path: str = "data.products"
    mode: Literal["cursor", "offset"] = "cursor"
    endpoint_template: str | None = None
    method: str = "GET"
    detail_endpoint_template: str | None = None  # GET, {id} placeholder; enriches candidates
    # cursor mode
    next_cursor_path: str | None = "data.pageInfo.endCursor"
    has_next_path: str | None = "data.pageInfo.hasNextPage"
    # offset mode
    page_size: int = 100
    total_path: str | None = None
    request_template: dict | None = None
    # Pagination param names differ by API (Syncee: from/size/category; CJ: pageNum/pageSize/
    # categoryId). paginate_by "offset" => position is an item offset (next += size);
    # "page" => position is a 1-based page number (next += 1).
    offset_param: str = "from"
    size_param: str = "size"
    category_param: str = "category"
    paginate_by: Literal["offset", "page"] = "offset"
    # Offset mode: scan each of these category ids in turn (overrides request_template's
    # `category`). Empty -> single scan using request_template as-is.
    categories: list[int] = Field(default_factory=list)
    # Cap products scanned per category (0 = unlimited) so the pool stays balanced across
    # categories of very different sizes.
    per_category_limit: int = 0


class ProductFieldMap(BaseModel):
    """Dotted paths (relative to one product object) for canonical raw product fields.

    Defaults are *placeholders* to be confirmed by discovery (spec §8.4). ``images`` and
    ``variants`` resolve to lists; ``images_item_field`` optionally pulls a subfield from
    each image object (e.g. ``url``).
    """

    id: str = "id"
    name: str = "title"
    url: str | None = "url"
    # If set, the product URL is built from this template ({id} placeholder) instead of a
    # response path — Syncee's product responses carry no user-facing page URL.
    url_template: str | None = None
    sku: str | None = "sku"
    brand: str | None = "brand"
    category: str | None = "category"
    subcategory: str | None = "subcategory"
    description: str | None = "description"
    currency: str | None = "currency"
    price: str | None = "price"
    default_currency_price: str | None = None  # cost in retailer currency (preferred)
    suggested_retail_price: str | None = "rrp"
    shipping_cost: str | None = "shipping.cost"
    shipping_min_days: str | None = "shipping.minDays"
    shipping_max_days: str | None = "shipping.maxDays"
    shipping_zones: str | None = None  # full per-country SHIPPING array (detail); overrides above
    stock_status: str | None = "stock.status"
    stock_quantity: str | None = "stock.quantity"
    images: str | None = "images"
    images_item_field: str | None = None
    main_image: str | None = "mainImage"
    variants: str | None = "variants"
    ships_from: str | None = "shipsFrom"
    added_at: str | None = "createdAt"
    updated_at: str | None = "updatedAt"
    active: str | None = "active"


class SupplierFieldMap(BaseModel):
    """Dotted paths (relative to one product object) for the nested supplier."""

    id: str = "supplier.id"
    name: str = "supplier.name"
    url: str | None = "supplier.url"
    country: str | None = "supplier.country"
    dispatch_countries: str | None = "supplier.dispatchCountries"
    ships_to_countries: str | None = "supplier.shipsToCountries"
    approval_required: str | None = "supplier.approvalRequired"
    rating: str | None = "supplier.rating"
    review_count: str | None = "supplier.reviewCount"
    catalog_count: str | None = "supplier.productCount"
    shipping_min_days: str | None = "supplier.shipping.minDays"
    shipping_max_days: str | None = "supplier.shipping.maxDays"
    shipping_policy_available: str | None = "supplier.hasShippingPolicy"
    return_policy_available: str | None = "supplier.hasReturnPolicy"
    contact_available: str | None = "supplier.hasContact"
    active: str | None = "supplier.active"


class SynceeMapping(BaseModel):
    """Full declarative mapping (loaded from ``config/syncee_mapping.yaml``)."""

    list: ListMapping = Field(default_factory=ListMapping)
    product: ProductFieldMap = Field(default_factory=ProductFieldMap)
    supplier: SupplierFieldMap = Field(default_factory=SupplierFieldMap)


def load_mapping(path: str | Path | None = None) -> SynceeMapping:
    """Load a mapping from YAML, falling back to placeholder defaults if absent."""
    path = Path(path or DEFAULT_MAPPING_PATH)
    if not path.exists():
        return SynceeMapping()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SynceeMapping.model_validate(data)


@dataclass
class MappedPage:
    products: list[dict]
    next_cursor: str | None = None
    has_next: bool = False
    raw_count: int = 0
    total: int | None = None
    warnings: list[str] = field(default_factory=list)


class SynceeResponseMapper:
    """Maps a raw Syncee list response into canonical raw products (spec §5.4)."""

    def __init__(self, mapping: SynceeMapping | None = None) -> None:
        self.mapping = mapping or SynceeMapping()

    def map_response(self, response: dict) -> MappedPage:
        """Extract + map every product in a list response."""
        m = self.mapping
        raw_products = get_path(response, m.list.products_path, default=[]) or []
        if not isinstance(raw_products, list):
            return MappedPage(products=[], warnings=["products_path did not resolve to a list"])

        mapped = [self.map_product(p) for p in raw_products]
        next_cursor = get_path(response, m.list.next_cursor_path)
        has_next = bool(get_path(response, m.list.has_next_path, default=False))
        total = get_path(response, m.list.total_path)
        return MappedPage(
            products=mapped,
            next_cursor=str(next_cursor) if next_cursor is not None else None,
            has_next=has_next,
            raw_count=len(raw_products),
            total=int(total) if isinstance(total, (int, float)) else None,
        )

    def map_product(self, product: dict) -> dict:
        """Map one raw product (with nested supplier) to the canonical raw schema."""
        pm = self.mapping.product
        sm = self.mapping.supplier

        images = get_path(product, pm.images, default=[]) or []
        if pm.images_item_field and isinstance(images, list):
            images = [
                get_path(img, pm.images_item_field) if isinstance(img, dict) else img
                for img in images
            ]

        product_id = get_path(product, pm.id)
        if pm.url_template and product_id is not None:
            url = pm.url_template.replace("{id}", str(product_id))
        else:
            url = get_path(product, pm.url)
        return {
            "id": product_id,
            "name": get_path(product, pm.name),
            "url": url,
            "sku": get_path(product, pm.sku),
            "brand": get_path(product, pm.brand),
            "category": get_path(product, pm.category),
            "subcategory": get_path(product, pm.subcategory),
            "description": get_path(product, pm.description),
            "currency": get_path(product, pm.currency),
            "price": get_path(product, pm.price),
            "default_currency_price": get_path(product, pm.default_currency_price),
            "suggested_retail_price": get_path(product, pm.suggested_retail_price),
            "shipping_cost": get_path(product, pm.shipping_cost),
            "shipping_min_days": get_path(product, pm.shipping_min_days),
            "shipping_max_days": get_path(product, pm.shipping_max_days),
            "shipping_zones": get_path(product, pm.shipping_zones),
            "stock_status": get_path(product, pm.stock_status),
            "stock_quantity": get_path(product, pm.stock_quantity),
            "images": images,
            "main_image": get_path(product, pm.main_image),
            "variants": get_path(product, pm.variants, default=[]) or [],
            "ships_from": get_path(product, pm.ships_from),
            "added_at": get_path(product, pm.added_at),
            "updated_at": get_path(product, pm.updated_at),
            "active": get_path(product, pm.active),
            "supplier": {
                "id": get_path(product, sm.id),
                "name": get_path(product, sm.name),
                "url": get_path(product, sm.url),
                "country": get_path(product, sm.country),
                "dispatch_countries": get_path(product, sm.dispatch_countries, default=[]),
                "ships_to_countries": get_path(product, sm.ships_to_countries, default=[]),
                "approval_required": get_path(product, sm.approval_required),
                "rating": get_path(product, sm.rating),
                "review_count": get_path(product, sm.review_count),
                "catalog_count": get_path(product, sm.catalog_count),
                "shipping_min_days": get_path(product, sm.shipping_min_days),
                "shipping_max_days": get_path(product, sm.shipping_max_days),
                "shipping_policy_available": get_path(product, sm.shipping_policy_available),
                "return_policy_available": get_path(product, sm.return_policy_available),
                "contact_available": get_path(product, sm.contact_available),
                "active": get_path(product, sm.active),
            },
        }
