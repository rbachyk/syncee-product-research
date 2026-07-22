"""Initial assortment selection (spec §26).

Greedy, deterministic selection of 18–24 shortlisted products balanced 6–8 per collection,
with supplier-concentration and duplicate-concept limits. Produces candidates only — no
product becomes ``Initial Assortment Selected`` without manual approval (spec §26.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AppConfig
from ..models import Collection
from .diversity import (
    Candidate,
    SelectionState,
    max_products_per_supplier,
    selection_score,
    violates_hard_constraints,
)


@dataclass
class SelectionResult:
    selected: list[Candidate] = field(default_factory=list)
    per_collection: dict[Collection, int] = field(default_factory=dict)
    per_supplier: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.selected)


def select_initial(candidates: list[Candidate], config: AppConfig) -> SelectionResult:
    """Select the initial assortment (spec §26).

    Candidates must already be shortlisted and eligible (the caller filters by review
    status). Selection is deterministic given the same inputs.
    """
    sel = config.selection
    per_supplier_max = max_products_per_supplier(sel.initial_total_max, sel.max_supplier_share_pct)
    state = SelectionState()
    result = SelectionResult()

    # Stable ordering: strongest first, then key for determinism on ties.
    pool = sorted(candidates, key=lambda c: (-c.product_score, c.product_key))

    while state.total < sel.initial_total_max:
        best: Candidate | None = None
        best_score = float("-inf")
        for c in pool:
            if c.product_key in {s.product_key for s in result.selected}:
                continue
            if violates_hard_constraints(
                c, state,
                per_collection_max=sel.target_per_collection_max,
                per_supplier_max=per_supplier_max,
            ):
                continue
            s = selection_score(c, state, per_collection_target_min=sel.target_per_collection_min)
            if s > best_score:
                best_score, best = s, c
        if best is None:
            break
        state.accept(best)
        result.selected.append(best)

    result.per_collection = dict(state.per_collection)
    result.per_supplier = dict(state.per_supplier)
    _annotate(result, sel)
    return result


def _annotate(result: SelectionResult, sel) -> None:
    if result.count < sel.initial_total_min:
        result.notes.append(
            f"Only {result.count} candidates selected (< target minimum "
            f"{sel.initial_total_min}); not enough eligible shortlisted products."
        )
    for collection in (Collection.KITCHEN_CONVENIENCE, Collection.DINING,
                       Collection.HOME_COMFORT, Collection.BATHROOM):
        n = result.per_collection.get(collection, 0)
        if n < sel.target_per_collection_min:
            result.notes.append(
                f"{collection.value}: {n} selected (< target {sel.target_per_collection_min})."
            )
