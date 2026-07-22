"""Deterministic collection classification (spec §25.4).

Applies, in order: (1) deterministic category mapping, (2) rule-based keyword scoring,
falling back to Practical Finds as the catch-all. Products below the confidence threshold
are flagged for manual review; an optional batch LLM fallback (:mod:`.llm_fallback`) may
refine them later but is never called per-raw-product (spec §25.4, §31).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..models import Collection
from .collections import CATEGORY_HINTS, KEYWORDS


@dataclass
class ClassificationResult:
    collection: Collection
    confidence: float
    method: str  # "category-map" | "keyword" | "catch-all"
    needs_review: bool = False


def _text(product: dict) -> str:
    return " ".join(
        str(product.get(k) or "")
        for k in ("product_name", "syncee_subcategory", "syncee_category", "description")
    ).lower()


def _category_map(product: dict) -> Collection | None:
    haystack = " ".join(
        str(product.get(k) or "") for k in ("syncee_category", "syncee_subcategory")
    ).lower()
    for fragment, collection in CATEGORY_HINTS.items():
        if fragment in haystack:
            return collection
    return None


def _keyword_scores(text: str) -> dict[Collection, int]:
    return {
        collection: sum(1 for kw in words if kw in text)
        for collection, words in KEYWORDS.items()
    }


def classify_product(product: dict, config: AppConfig) -> ClassificationResult:
    """Classify a product into exactly one collection (spec §25.4)."""
    threshold = config.classification.minimum_confidence

    # 0. Scanned subcategory -> collection (most reliable; we know what we searched).
    subcat = product.get("syncee_subcategory")
    if subcat:
        mapped_name = config.classification.category_collection_map.get(subcat)
        if mapped_name:
            return ClassificationResult(
                Collection(mapped_name), 0.95, "subcategory-map", needs_review=False
            )

    # 1. Deterministic category mapping (highest confidence).
    mapped = _category_map(product)
    if mapped is not None:
        return ClassificationResult(mapped, 0.9, "category-map", needs_review=False)

    # 2. Rule-based keyword scoring.
    text = _text(product)
    scores = _keyword_scores(text)
    best = max(scores, key=lambda c: scores[c])
    best_score = scores[best]
    total = sum(scores.values())

    if best_score > 0:
        confidence = round(0.5 + 0.5 * (best_score / total), 2) if total else 0.5
        return ClassificationResult(
            best, confidence, "keyword", needs_review=confidence < threshold
        )

    # 3. Catch-all: Practical Finds (spec §25.3). Low confidence -> manual review.
    return ClassificationResult(
        Collection.PRACTICAL_FINDS, 0.4, "catch-all", needs_review=0.4 < threshold
    )
