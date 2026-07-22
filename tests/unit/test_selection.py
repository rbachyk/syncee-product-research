"""Unit tests for selection + diversity (spec §26, §29, §41.1)."""

from syncee_scanner.config import load_config
from syncee_scanner.models import Collection
from syncee_scanner.selection.diversity import (
    Candidate,
    SelectionState,
    max_products_per_supplier,
    violates_hard_constraints,
)
from syncee_scanner.selection.initial import select_initial
from syncee_scanner.selection.new_arrivals import select_new_arrivals

COLLECTIONS = [
    Collection.KITCHEN_CONVENIENCE,
    Collection.HOME_COMFORT,
    Collection.PRACTICAL_FINDS,
]


def cfg():
    return load_config()


def make_pool(per_collection=10, suppliers=5, base_score=80.0) -> list[Candidate]:
    cands = []
    for col in COLLECTIONS:
        for i in range(per_collection):
            cands.append(
                Candidate(
                    product_key=f"pid:{col.name}:{i}",
                    supplier_key=f"sid:{i % suppliers}",
                    collection=col,
                    product_score=base_score + (i % 7),
                    price=5.0 + i * 2,
                    content_potential=0.7,
                    name=f"{col.value} widget {i}",
                )
            )
    return cands


class TestDiversityHelpers:
    def test_max_per_supplier(self):
        # 30% of 24 -> 7
        assert max_products_per_supplier(24, 30) == 7
        assert max_products_per_supplier(4, 30) == 1


class TestSupplierPriceDuplicate:
    def _state_with(self, c: Candidate) -> SelectionState:
        s = SelectionState()
        s.accept(c)
        return s

    def test_same_supplier_same_price_blocked(self):
        first = Candidate("pid:1", "sid:kaimok", Collection.PRACTICAL_FINDS, 70.0, 186.05,
                          name="Estanteria Firenze")
        dup = Candidate("pid:2", "sid:kaimok", Collection.PRACTICAL_FINDS, 69.0, 186.05,
                        name="Revistero Faenza")  # different concept, same line
        state = self._state_with(first)
        assert violates_hard_constraints(
            dup, state, per_collection_max=10, per_supplier_max=9
        )

    def test_same_supplier_different_price_allowed(self):
        first = Candidate("pid:1", "sid:kaimok", Collection.PRACTICAL_FINDS, 70.0, 186.05,
                          name="Estanteria Firenze")
        other = Candidate("pid:2", "sid:kaimok", Collection.PRACTICAL_FINDS, 69.0, 92.0,
                          name="Small shelf")
        state = self._state_with(first)
        assert not violates_hard_constraints(
            other, state, per_collection_max=10, per_supplier_max=9
        )

    def test_unknown_price_not_treated_as_duplicate(self):
        first = Candidate("pid:1", "sid:x", Collection.PRACTICAL_FINDS, 70.0, None, name="a")
        other = Candidate("pid:2", "sid:x", Collection.PRACTICAL_FINDS, 69.0, None, name="b")
        state = self._state_with(first)
        assert not violates_hard_constraints(
            other, state, per_collection_max=10, per_supplier_max=9
        )


class TestInitial:
    def test_selects_within_range_and_balanced(self):
        r = select_initial(make_pool(), cfg())
        c = cfg().selection
        assert c.initial_total_min <= r.count <= c.initial_total_max
        for col in COLLECTIONS:
            assert r.per_collection.get(col, 0) >= c.target_per_collection_min

    def test_supplier_concentration_capped(self):
        r = select_initial(make_pool(suppliers=2), cfg())
        cap = max_products_per_supplier(
            cfg().selection.initial_total_max, cfg().selection.max_supplier_share_pct
        )
        assert all(n <= cap for n in r.per_supplier.values())

    def test_deterministic(self):
        a = select_initial(make_pool(), cfg())
        b = select_initial(make_pool(), cfg())
        assert [c.product_key for c in a.selected] == [c.product_key for c in b.selected]

    def test_duplicate_concepts_limited(self):
        # Many identical concepts from different suppliers.
        pool = [
            Candidate(f"pid:{i}", f"sid:{i}", Collection.PRACTICAL_FINDS, 90.0, 10.0, 0.7,
                     name="Identical Magic Gadget")
            for i in range(10)
        ]
        r = select_initial(pool, cfg())
        assert r.count <= 2  # no more than two near-identical concepts

    def test_thin_pool_notes(self):
        pool = make_pool(per_collection=2)  # only 6 total
        r = select_initial(pool, cfg())
        assert r.count == 6
        assert any("minimum" in n for n in r.notes)


class TestNewArrivals:
    def test_four_product_balanced_batch(self):
        r = select_new_arrivals(make_pool(), cfg())
        assert r.count == 4
        # one per collection covered by preferred composition
        assert set(r.per_collection.keys()) >= set(COLLECTIONS)

    def test_supplier_cap_two(self):
        r = select_new_arrivals(make_pool(suppliers=1), cfg())
        assert all(n <= 2 for n in r.per_supplier.values())
        assert r.count <= 2  # only one supplier, capped at 2

    def test_relaxes_when_collection_missing(self):
        pool = [
            Candidate(f"pid:{i}", f"sid:{i}", Collection.KITCHEN_CONVENIENCE, 85.0, 9.0, 0.6,
                     name=f"kitchen thing {i}")
            for i in range(6)
        ]
        r = select_new_arrivals(pool, cfg())
        assert r.count == 4  # filled from available collection


class TestPriceCeiling:
    def test_build_candidates_filters_by_max_retail(self):
        from syncee_scanner.runs.persistence import InMemoryPersistence
        from syncee_scanner.selection.service import build_candidates

        c = cfg()
        c.selection.max_retail_price = 100
        p = InMemoryPersistence()
        p.suppliers["sid:1"] = {"id": 1, "Supplier Key": "sid:1"}
        # two shortlisted products: one affordable, one premium
        for key, retail in [("pid:cheap", 80.0), ("pid:premium", 250.0)]:
            p.products[key] = {
                "id": len(p.products) + 10, "Product Key": key, "Supplier": [1],
                "Review Status": "Shortlisted", "Selection Status": "Not Selected",
                "Collection": "Kitchen Convenience", "Product Score": 70.0,
                "Proposed Retail Price": retail, "Product Name": "x",
            }
        cands = build_candidates(p, config=c)
        keys = {x.product_key for x in cands}
        assert "pid:cheap" in keys and "pid:premium" not in keys


class TestRejectAndBackfill:
    def _persistence_with(self, selected, pool):
        from syncee_scanner.models import ProductReviewStatus, SelectionStatus
        from syncee_scanner.runs.persistence import InMemoryPersistence
        p = InMemoryPersistence()
        rid = 0
        for grp, status in ((selected, SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE.value),
                            (pool, SelectionStatus.NOT_SELECTED.value)):
            for c in grp:
                rid += 1
                p.products[c.product_key] = {
                    "id": rid, "Product Key": c.product_key, "Product Name": c.name,
                    "Collection": c.collection.value, "Product Score": c.product_score,
                    "Proposed Retail Price": c.price, "Supplier": [rid + 1000],
                    "Review Status": ProductReviewStatus.SHORTLISTED.value,
                    "Selection Status": status,
                }
                p.suppliers[f"s{rid}"] = {"id": rid + 1000, "Supplier Key": c.supplier_key}
        return p

    def test_backfills_rejected_slots_without_touching_kept(self):
        from syncee_scanner.selection.service import reject_and_backfill
        col = Collection.KITCHEN_CONVENIENCE
        # 10 selected in KC; reject 2 → expect 2 backfilled from the pool, kept ones untouched.
        selected = [Candidate(f"sel:{i}", f"sid:{i}", col, 80.0 - i, 40.0 + i, 0.7, f"kept {i}")
                    for i in range(10)]
        pool = [Candidate(f"pool:{i}", f"psid:{i}", col, 60.0 + i, 50.0 + i, 0.7, f"new {i}")
                for i in range(5)]
        p = self._persistence_with(selected, pool)
        res = reject_and_backfill(p, cfg(), ["sel:0", "sel:1"])
        assert set(res["rejected"]) == {"sel:0", "sel:1"}
        assert len(res["added"]) == 2                       # refilled to 10
        assert res["per_collection"]["Kitchen Convenience"] == 10
        # rejected are Manually Rejected + Not Selected; a kept one is untouched
        assert p.products["sel:0"]["Review Status"] == "Manually Rejected"
        assert p.products["sel:0"]["Selection Status"] == "Not Selected"
        assert p.products["sel:5"]["Selection Status"] == "Initial Assortment Candidate"
        assert len(p.manual_decisions) == 2
