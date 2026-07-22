"""Diversity constraints and selection-score adjustments (spec §26.3–§26.4, §29.4).

Shared machinery for both the initial assortment and new-arrivals selectors: a candidate
model, concept-signature duplicate detection, per-supplier caps, and the balance/diversity
adjustments that shape the greedy selection score (spec §26.4).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..extraction.normalization import slugify
from ..models import Collection

# Low-signal words dropped when building a product's concept signature.
_STOPWORDS = {
    "the", "a", "an", "for", "with", "and", "of", "set", "pcs", "pack", "new", "premium",
    "steel", "stainless", "plastic", "silicone", "kitchen", "home", "portable", "mini",
}


@dataclass
class Candidate:
    """A scored, shortlisted product eligible for selection."""

    product_key: str
    supplier_key: str
    collection: Collection
    product_score: float
    price: float | None = None
    content_potential: float = 0.5
    name: str = ""

    @property
    def concept_signature(self) -> str:
        raw = re.split(r"[^a-z0-9]+", slugify(self.name))
        tokens = [t for t in raw if t and t not in _STOPWORDS]
        return "-".join(sorted(tokens[:4])) or self.product_key


def max_products_per_supplier(total_max: int, max_share_pct: float) -> int:
    """Hard per-supplier cap from the diversity share (spec §26.3)."""
    return max(1, math.floor(max_share_pct / 100.0 * total_max))


def _supplier_price_key(c: Candidate) -> tuple[str, str, int] | None:
    """A (supplier, collection, rounded-price) key identifying same-line product variants.

    Two items from the same supplier at the same price within one collection are almost always
    variants of a single product line (e.g. a wall shelf and a magazine rack from the same
    design series); picking both wastes an assortment slot on a near-duplicate look. Returns
    None when price is unknown.
    """
    if c.price is None:
        return None
    return (c.supplier_key, c.collection.value, round(c.price))


@dataclass
class SelectionState:
    """Running tallies used to score/constrain the next pick."""

    per_collection: dict[Collection, int] = field(default_factory=dict)
    per_supplier: dict[str, int] = field(default_factory=dict)
    per_concept: dict[str, int] = field(default_factory=dict)
    supplier_price_keys: set[tuple[str, int]] = field(default_factory=set)
    prices: list[float] = field(default_factory=list)
    total: int = 0

    def accept(self, c: Candidate) -> None:
        self.per_collection[c.collection] = self.per_collection.get(c.collection, 0) + 1
        self.per_supplier[c.supplier_key] = self.per_supplier.get(c.supplier_key, 0) + 1
        self.per_concept[c.concept_signature] = self.per_concept.get(c.concept_signature, 0) + 1
        spk = _supplier_price_key(c)
        if spk is not None:
            self.supplier_price_keys.add(spk)
        if c.price is not None:
            self.prices.append(c.price)
        self.total += 1


def violates_hard_constraints(
    c: Candidate,
    state: SelectionState,
    *,
    per_collection_max: int,
    per_supplier_max: int,
    max_duplicate_concepts: int = 2,
) -> bool:
    """Whether adding ``c`` would breach a hard diversity constraint (spec §26.3)."""
    if state.per_collection.get(c.collection, 0) >= per_collection_max:
        return True
    if state.per_supplier.get(c.supplier_key, 0) >= per_supplier_max:
        return True
    if state.per_concept.get(c.concept_signature, 0) >= max_duplicate_concepts:
        return True
    if _supplier_price_key(c) in state.supplier_price_keys:
        return True  # same supplier + same price = product-line near-duplicate
    return False


def selection_score(
    c: Candidate, state: SelectionState, *, per_collection_target_min: int
) -> float:
    """Dynamic selection score (spec §26.4).

    Base product score, plus adjustments that reward under-filled collections, supplier and
    price diversity and content potential, minus a duplicate-concept penalty.
    """
    score = c.product_score

    # Collection balance: boost collections still below their minimum target.
    filled = state.per_collection.get(c.collection, 0)
    if filled < per_collection_target_min:
        score += 6 * (per_collection_target_min - filled) / per_collection_target_min

    # Supplier diversity: penalize repeat suppliers.
    score -= 4 * state.per_supplier.get(c.supplier_key, 0)

    # Price-point balance: reward prices away from the current mean.
    if c.price is not None and state.prices:
        mean = sum(state.prices) / len(state.prices)
        spread = abs(c.price - mean) / max(1.0, mean)
        score += min(3.0, 3.0 * spread)

    # Content potential.
    score += 4 * c.content_potential

    # Duplicate-concept penalty.
    score -= 8 * state.per_concept.get(c.concept_signature, 0)

    return score
