"""Deterministic field normalization for publish-prep (pure functions).

Fills the *structured* Shopify fields that don't need language understanding — vendor, product
type, material, dimensions, weight, handle, baseline tags — parsed from the existing product
row. The language-heavy fields (clean English title, description, SEO copy) are the LLM's job
(:mod:`.seo`); this module deliberately does no translation.
"""

from __future__ import annotations

import re

from ..extraction.normalization import slugify
from ..models import Collection

# Collection → Shopify product-type default (refined by the Syncee category label when present).
_TYPE_BY_COLLECTION = {
    Collection.KITCHEN_CONVENIENCE.value: "Kitchen & Dining",
    Collection.HOME_COMFORT.value: "Home Décor",
    Collection.PRACTICAL_FINDS.value: "Home Storage & Organization",
}

# Material keywords (multilingual-ish stems) → canonical material label.
_MATERIALS: tuple[tuple[str, str], ...] = (
    (r"stainless steel|edelstahl|acier inoxydable", "Stainless steel"),
    (r"\btitanium|titan\b", "Titanium"),
    (r"\bcopper|kupfer|cuivre|rame\b", "Copper"),
    (r"\bbrass|messing|laiton|ottone\b", "Brass"),
    (r"olive ?wood|olivenholz|legno d'ulivo|bois d'olivier|ulivo", "Olive wood"),
    (r"\boak|eiche|chêne|rovere\b", "Oak"),
    (r"\bwalnut|nussbaum|noyer|noce\b", "Walnut"),
    (r"\bbamboo|bambus|bambou|bambù\b", "Bamboo"),
    (r"\bwood|holz|bois|legno|madera|drewn\b", "Wood"),
    (r"\bceramic|keramik|céramique|ceramica|cerámica\b", "Ceramic"),
    (r"\bporcelain|porzellan|porcelaine|porcellana\b", "Porcelain"),
    (r"\bglass|glas|verre|vetro|cristal\b", "Glass"),
    (r"\bleather|leder|cuir|cuero|pelle\b", "Leather"),
    (r"\bcotton|baumwolle|coton|cotone|algodón\b", "Cotton"),
    (r"\blinen|leinen|\blin\b|lino\b", "Linen"),
    (r"\bwool|wolle|laine|lana\b", "Wool"),
    (r"\bmetal|metall|métal|metallo\b", "Metal"),
)

_DIM = re.compile(
    r"(\bØ?\s?\d{1,3}(?:[.,]\d)?\s?[x×]\s?\d{1,3}(?:[.,]\d)?(?:\s?[x×]\s?\d{1,3}(?:[.,]\d)?)?\s?(?:cm|mm|m\b))",
    re.IGNORECASE,
)
_WEIGHT = re.compile(r"(\b\d{1,4}(?:[.,]\d{1,3})?\s?(?:g|kg|gr|gramm|grammes?)\b)", re.IGNORECASE)


def _text(row: dict) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in ("Product Name", "Description", "Syncee Category", "Syncee Subcategory")
    )


def parse_material(text: str) -> str | None:
    low = text.lower()
    for pattern, label in _MATERIALS:
        if re.search(pattern, low):
            return label
    return None


def parse_dimensions(text: str) -> str | None:
    m = _DIM.search(text)
    return re.sub(r"\s+", "", m.group(1)).replace("x", " × ").replace("×", " × ") if m else None


def parse_weight(text: str) -> str | None:
    m = _WEIGHT.search(text)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def product_type_for(row: dict) -> str:
    return _TYPE_BY_COLLECTION.get(row.get("Collection") or "", "Home")


def base_tags(row: dict, material: str | None) -> list[str]:
    """Deterministic seed tags — the SEO step adds English keyword tags on top.

    Deliberately excludes the raw Syncee category: it's often German / supplier jargon
    (e.g. "Cocktail- & Barzubehörsets") and carries no meaning in the RB Home store.
    """
    tags: list[str] = []
    for candidate in (row.get("Collection"), material, "RB Home"):
        c = (candidate or "").strip()
        if c and c not in tags:
            tags.append(c)
    return tags


def handle_from(title: str, fallback: str) -> str:
    """Shopify URL handle from the (clean) title, falling back to the product key slug."""
    return slugify(title) or slugify(fallback) or "product"


def normalize_fields(row: dict) -> dict:
    """Return the deterministic publish-prep fields for a product row."""
    text = _text(row)
    material = parse_material(text)
    return {
        "Vendor": "RB Home",
        "Product Type": product_type_for(row),
        "Material": material,
        "Dimensions": parse_dimensions(text),
        "Weight": parse_weight(text),
        # Publish Tags is content-owned (base + English SEO keywords) — set by the SEO step,
        # not here, so image-only re-runs never clobber the generated tags.
        "Original Image URL": row.get("Main Image URL") or None,
    }
