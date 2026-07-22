"""Baserow operational views (spec §30).

Declares the review views for suppliers/products/scan-runs and builds their filter
payloads by resolving field IDs + single-select option IDs against a live field map. Views
are operational conveniences only — business logic never depends on view filters (spec
§30.3). The filter-payload builder is pure and unit-tested; the create calls are thin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..observability.errors import BaserowError
from .schemas import T_PRODUCTS, T_SCAN_RUNS, T_SUPPLIERS


@dataclass(frozen=True)
class FilterSpec:
    field_name: str
    type: str  # "single_select_equal" | "boolean" | "empty" | "not_empty"
    value: object = None


@dataclass(frozen=True)
class ViewSpec:
    name: str
    table: str
    filters: tuple[FilterSpec, ...] = field(default_factory=tuple)
    view_type: str = "grid"  # "grid" | "gallery"
    cover_field: str | None = None  # gallery card cover image field (a FILE field)


# Subset of spec §30 views expressible as simple, robust filters. Views without a clean
# server-side filter (e.g. free-text views) are created as plain grids for manual sorting.
VIEW_SPECS: tuple[ViewSpec, ...] = (
    # Suppliers (spec §30.1)
    ViewSpec("Supplier Review", T_SUPPLIERS,
             (FilterSpec("Eligibility Status", "single_select_equal", "Manual Review"),)),
    ViewSpec("Approved Suppliers", T_SUPPLIERS,
             (FilterSpec("Eligibility Status", "single_select_equal", "Approved"),)),
    ViewSpec("Rejected Suppliers", T_SUPPLIERS,
             (FilterSpec("Eligibility Status", "single_select_equal", "Scored Rejected"),)),
    ViewSpec("Missing Shipping Data", T_SUPPLIERS,
             (FilterSpec("Shipping Max Days", "empty"),)),
    ViewSpec("Manual Overrides", T_SUPPLIERS,
             (FilterSpec("Manual Override", "not_empty"),)),
    ViewSpec("Inactive Suppliers", T_SUPPLIERS,
             (FilterSpec("Active", "boolean", False),)),
    # Products (spec §30.2)
    ViewSpec("All Active Products", T_PRODUCTS, (FilterSpec("Active", "boolean", True),)),
    ViewSpec("New Products", T_PRODUCTS, (FilterSpec("Is New", "boolean", True),)),
    ViewSpec("Eligible Products", T_PRODUCTS,
             (FilterSpec("Supplier Eligible", "boolean", True),)),
    ViewSpec("Excluded by Supplier", T_PRODUCTS,
             (FilterSpec("Review Status", "single_select_equal", "Excluded by Supplier"),)),
    ViewSpec("Product Review", T_PRODUCTS,
             (FilterSpec("Review Status", "single_select_equal", "Manual Review"),)),
    ViewSpec("Initial Assortment Candidates", T_PRODUCTS,
             (FilterSpec("Selection Status", "single_select_equal",
                         "Initial Assortment Candidate"),)),
    ViewSpec("New Arrival Candidates", T_PRODUCTS,
             (FilterSpec("Selection Status", "single_select_equal", "New Arrival Candidate"),)),
    ViewSpec("Missing Margin Data", T_PRODUCTS,
             (FilterSpec("Margin Status", "single_select_equal", "Incomplete"),)),
    ViewSpec("High Risk", T_PRODUCTS, (FilterSpec("Risk Flags", "not_empty"),)),
    ViewSpec("Inactive Products", T_PRODUCTS, (FilterSpec("Active", "boolean", False),)),
    ViewSpec("Published Products", T_PRODUCTS,
             (FilterSpec("Selection Status", "single_select_equal", "Published"),)),
    # Publish-prep QA dashboard — a gallery of assortment candidates, processed image as the
    # card cover, so image transforms + SEO can be visually signed off before publishing.
    ViewSpec("Publish Prep — Gallery", T_PRODUCTS,
             (FilterSpec("Selection Status", "single_select_equal",
                         "Initial Assortment Candidate"),),
             view_type="gallery", cover_field="Processed Image"),
    ViewSpec("Needs Attention", T_PRODUCTS,
             (FilterSpec("Publish-Prep Status", "single_select_equal", "Needs Attention"),)),
    # Scan runs (spec §30.3)
    ViewSpec("Active Runs", T_SCAN_RUNS,
             (FilterSpec("Status", "single_select_equal", "Running"),)),
    ViewSpec("Failed Runs", T_SCAN_RUNS,
             (FilterSpec("Status", "single_select_equal", "Failed"),)),
    ViewSpec("Completed Runs", T_SCAN_RUNS,
             (FilterSpec("Status", "single_select_equal", "Completed"),)),
    ViewSpec("Unverified Completeness", T_SCAN_RUNS,
             (FilterSpec("Completeness Status", "single_select_equal", "Unverified"),)),
)

# Map our internal filter type -> Baserow filter type name.
_BASEROW_FILTER_TYPE = {
    "single_select_equal": "single_select_equal",
    "boolean": "boolean",
    "empty": "empty",
    "not_empty": "not_empty",
}


def _option_id(field_meta: dict, value: str) -> int:
    for opt in field_meta.get("select_options", []):
        if opt.get("value") == value:
            return opt["id"]
    raise BaserowError(
        f"Field '{field_meta.get('name')}' has no select option '{value}'"
    )


def build_filter_payloads(view: ViewSpec, field_map: dict[str, dict]) -> list[dict]:
    """Resolve a view's filters into Baserow filter-create payloads (pure)."""
    payloads: list[dict] = []
    for f in view.filters:
        meta = field_map.get(f.field_name)
        if meta is None:
            raise BaserowError(f"View '{view.name}' references unknown field '{f.field_name}'")
        payload = {"field": meta["id"], "type": _BASEROW_FILTER_TYPE[f.type]}
        if f.type == "single_select_equal":
            payload["value"] = str(_option_id(meta, f.value))
        elif f.type == "boolean":
            payload["value"] = "1" if f.value else "0"
        else:  # empty / not_empty
            payload["value"] = ""
        payloads.append(payload)
    return payloads
