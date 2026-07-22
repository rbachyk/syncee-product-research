"""New-arrivals batch selection (spec §29).

Builds a small (default 4) batch of newly-discovered, shortlisted candidates with a
preferred composition of one product per collection plus the highest-value remaining
candidate, honoring per-supplier (≤2) and duplicate-concept limits (spec §29.3–§29.4).
Candidates only — manual approval is still required (spec §29.6).
"""

from __future__ import annotations

from ..config import AppConfig
from ..models import Collection
from .diversity import Candidate, SelectionState, selection_score, violates_hard_constraints
from .initial import SelectionResult

_PREFERRED_ORDER = (
    Collection.KITCHEN_CONVENIENCE,
    Collection.HOME_COMFORT,
    Collection.PRACTICAL_FINDS,
)


def select_new_arrivals(candidates: list[Candidate], config: AppConfig) -> SelectionResult:
    """Select a new-arrivals batch (spec §29.3)."""
    batch_size = config.selection.new_arrivals_batch_size
    state = SelectionState()
    result = SelectionResult()
    chosen: set[str] = set()

    pool = sorted(candidates, key=lambda c: (-c.product_score, c.product_key))

    def pick(predicate) -> Candidate | None:
        for c in pool:
            if c.product_key in chosen:
                continue
            if not predicate(c):
                continue
            if violates_hard_constraints(
                c, state, per_collection_max=batch_size, per_supplier_max=2,
            ):
                continue
            return c
        return None

    # 1. One strong candidate per collection, in preferred order (spec §29.3).
    for collection in _PREFERRED_ORDER:
        if state.total >= batch_size:
            break
        best = pick(lambda c, col=collection: c.collection == col)
        if best is not None:
            state.accept(best)
            result.selected.append(best)
            chosen.add(best.product_key)

    # 2. Fill remaining slots with the highest-value remaining candidates (spec §29.3).
    while state.total < batch_size:
        best: Candidate | None = None
        best_score = float("-inf")
        for c in pool:
            if c.product_key in chosen:
                continue
            if violates_hard_constraints(
                c, state, per_collection_max=batch_size, per_supplier_max=2,
            ):
                continue
            s = selection_score(c, state, per_collection_target_min=1)
            if s > best_score:
                best_score, best = s, c
        if best is None:
            break
        state.accept(best)
        result.selected.append(best)
        chosen.add(best.product_key)

    result.per_collection = dict(state.per_collection)
    result.per_supplier = dict(state.per_supplier)
    if result.count < batch_size:
        result.notes.append(
            f"Only {result.count}/{batch_size} new-arrival candidates found; "
            "relaxed composition (spec §29.3)."
        )
    return result
