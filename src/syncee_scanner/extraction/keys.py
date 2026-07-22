"""Stable application-level key generation (spec §10.1, §11.1).

Keys are deterministic and follow a strict priority ladder so the same entity always
resolves to the same key across runs (spec §37.1/§37.2). A name alone is *never* unique
(spec §10.1, §11.1). Each key is prefixed with the strategy that produced it so its
provenance is auditable.
"""

from __future__ import annotations

import hashlib

from .normalization import normalize_country, normalize_text, normalize_url, slugify


def _hash(*parts: str) -> str:
    joined = "|".join(p for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:20]


def supplier_key(
    *,
    syncee_supplier_id: str | None = None,
    supplier_url: str | None = None,
    supplier_name: str | None = None,
    location_country: str | None = None,
) -> str:
    """Return a stable Supplier Key using the spec §10.1 priority ladder.

    Priority: (1) Syncee supplier ID, (2) normalized supplier URL,
    (3) deterministic hash of normalized name + country. Raises if none is usable.
    """
    if syncee_supplier_id:
        sid = normalize_text(str(syncee_supplier_id))
        if sid:
            return f"sid:{sid}"

    url = normalize_url(supplier_url)
    if url:
        return f"surl:{_hash(url)}"

    name_slug = slugify(supplier_name)
    if name_slug:
        country = normalize_country(location_country) or ""
        return f"shash:{_hash(name_slug, slugify(country))}"

    raise ValueError("Cannot build supplier key: no id, url or name provided")


def variant_signature(variants) -> str:
    """Deterministic signature of a product's variants for key/fingerprint use."""
    if not variants:
        return ""
    tokens: list[str] = []
    for v in variants:
        if isinstance(v, dict):
            token = ":".join(
                str(v.get(k, "")) for k in ("sku", "id", "option", "title")
            )
        else:
            token = str(v)
        tokens.append(slugify(token))
    return _hash(*sorted(t for t in tokens if t))


def product_key(
    *,
    supplier_key: str,
    syncee_product_id: str | None = None,
    supplier_sku: str | None = None,
    product_url: str | None = None,
    product_name: str | None = None,
    variants=None,
) -> str:
    """Return a stable Product Key using the spec §11.1 priority ladder.

    Priority: (1) Syncee product ID, (2) supplier key + supplier SKU,
    (3) normalized canonical product URL, (4) hash of supplier key + normalized name +
    variant signature. Product name alone is never treated as unique.
    """
    if not supplier_key:
        raise ValueError("supplier_key is required to build a product key")

    if syncee_product_id:
        pid = normalize_text(str(syncee_product_id))
        if pid:
            return f"pid:{pid}"

    sku = normalize_text(supplier_sku)
    if sku:
        return f"psku:{_hash(supplier_key, sku)}"

    url = normalize_url(product_url)
    if url:
        return f"purl:{_hash(url)}"

    name_slug = slugify(product_name)
    if name_slug:
        return f"phash:{_hash(supplier_key, name_slug, variant_signature(variants))}"

    raise ValueError("Cannot build product key: no id, sku, url or name provided")
