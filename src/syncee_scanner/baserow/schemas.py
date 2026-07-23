"""Baserow table & field definitions (spec §9–§15).

A single declarative description of the ``RB Home Product Research`` database. It drives:

  * the setup helper that creates tables + fields + select options;
  * pre-scan schema validation (spec §16 / §43.3) that raises BASEROW_SCHEMA_MISMATCH
    when required fields are missing.

Baserow field IDs are preferred internally (spec §16.2); this module names fields, while
the client resolves names -> IDs once at startup so visible renames do not break scans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import (
    BatchStatus,
    BatchType,
    Collection,
    CompletenessStatus,
    DecisionValue,
    EntityType,
    HardGateStatus,
    ManualOverride,
    MarginStatus,
    ProductReviewStatus,
    PublishPrepStatus,
    RunStatus,
    RunType,
    SelectionStatus,
    SupplierEligibility,
)

DATABASE_NAME = "RB Home Product Research"


class FieldType(str, Enum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    URL = "url"
    BOOLEAN = "boolean"
    NUMBER = "number"
    DATE = "date"
    SINGLE_SELECT = "single_select"
    LINK_ROW = "link_row"
    FILE = "file"


def _opts(enum_cls: type[Enum]) -> list[str]:
    return [e.value for e in enum_cls]


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: FieldType
    required: bool = False
    select_options: list[str] = field(default_factory=list)
    link_table: str | None = None
    number_decimals: int = 0
    primary: bool = False


@dataclass(frozen=True)
class TableDef:
    name: str
    fields: list[FieldDef]

    @property
    def primary_field(self) -> FieldDef:
        return next((f for f in self.fields if f.primary), self.fields[0])


# --- Table names -------------------------------------------------------------------

T_SUPPLIERS = "Suppliers"
T_PRODUCTS = "Products"
T_SCAN_RUNS = "Scan Runs"
T_PRODUCT_CHANGES = "Product Changes"
T_MANUAL_DECISIONS = "Manual Decisions"
T_SELECTION_BATCHES = "Selection Batches"


# --- Suppliers (spec §10.2) --------------------------------------------------------

SUPPLIERS = TableDef(
    T_SUPPLIERS,
    [
        FieldDef("Supplier Key", FieldType.TEXT, required=True, primary=True),
        FieldDef("Source", FieldType.TEXT),  # multi-source origin (Syncee, CJ, …)
        FieldDef("Syncee Supplier ID", FieldType.TEXT),
        FieldDef("Supplier Name", FieldType.TEXT, required=True),
        FieldDef("Supplier URL", FieldType.URL, required=True),
        FieldDef("Location Country", FieldType.TEXT),
        FieldDef("Dispatch Countries", FieldType.LONG_TEXT),
        FieldDef("Ships To Countries", FieldType.LONG_TEXT),
        FieldDef("Approval Required", FieldType.BOOLEAN),
        FieldDef("Supplier Rating", FieldType.NUMBER, number_decimals=2),
        FieldDef("Review Count", FieldType.NUMBER),
        FieldDef("Catalog Product Count", FieldType.NUMBER),
        FieldDef("Relevant Product Count", FieldType.NUMBER, required=True),
        FieldDef("Shipping Min Days", FieldType.NUMBER),
        FieldDef("Shipping Max Days", FieldType.NUMBER),
        FieldDef("Shipping Policy Available", FieldType.BOOLEAN),
        FieldDef("Return Policy Available", FieldType.BOOLEAN),
        FieldDef("Contact Information Available", FieldType.BOOLEAN),
        FieldDef("Data Completeness %", FieldType.NUMBER, required=True, number_decimals=1),
        FieldDef("First Seen At", FieldType.DATE, required=True),
        FieldDef("Last Seen At", FieldType.DATE, required=True),
        FieldDef("Last Changed At", FieldType.DATE),
        FieldDef("Active", FieldType.BOOLEAN, required=True),
        FieldDef(
            "Hard Gate Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(HardGateStatus),
        ),
        FieldDef("Supplier Score", FieldType.NUMBER, number_decimals=1),
        FieldDef("Supplier Score Version", FieldType.TEXT),
        FieldDef(
            "Eligibility Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(SupplierEligibility),
        ),
        FieldDef("Reason Codes", FieldType.LONG_TEXT),
        FieldDef(
            "Manual Override", FieldType.SINGLE_SELECT,
            select_options=_opts(ManualOverride),
        ),
        FieldDef("Manual Notes", FieldType.LONG_TEXT),
        FieldDef("Record Fingerprint", FieldType.TEXT),
        FieldDef("Raw Data", FieldType.LONG_TEXT),
        FieldDef("Last Scan Run", FieldType.LINK_ROW, link_table=T_SCAN_RUNS),
    ],
)


# --- Products (spec §11.2) ---------------------------------------------------------

PRODUCTS = TableDef(
    T_PRODUCTS,
    [
        FieldDef("Product Key", FieldType.TEXT, required=True, primary=True),
        FieldDef("Source", FieldType.TEXT),  # multi-source origin (Syncee, CJ, …)
        FieldDef("Syncee Product ID", FieldType.TEXT),
        FieldDef("Product Name", FieldType.TEXT, required=True),
        FieldDef("Product URL", FieldType.URL, required=True),
        FieldDef("Supplier", FieldType.LINK_ROW, required=True, link_table=T_SUPPLIERS),
        FieldDef("Supplier SKU", FieldType.TEXT),
        FieldDef("Brand", FieldType.TEXT),
        FieldDef("Syncee Category", FieldType.TEXT),
        FieldDef("Syncee Subcategory", FieldType.TEXT),
        FieldDef("Description", FieldType.LONG_TEXT),
        FieldDef("Currency", FieldType.TEXT),
        FieldDef("Supplier Price", FieldType.NUMBER, number_decimals=2),
        FieldDef("Suggested Retail Price", FieldType.NUMBER, number_decimals=2),
        FieldDef("Proposed Retail Price", FieldType.NUMBER, number_decimals=2),
        FieldDef("Shipping Cost", FieldType.NUMBER, number_decimals=2),
        FieldDef("Shipping Cost Known", FieldType.BOOLEAN, required=True),
        FieldDef("Estimated Landed Cost", FieldType.NUMBER, number_decimals=2),
        FieldDef("Estimated Margin Amount", FieldType.NUMBER, number_decimals=2),
        FieldDef("Estimated Margin Pct", FieldType.NUMBER, number_decimals=1),
        FieldDef(
            "Margin Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(MarginStatus),
        ),
        FieldDef("Stock Status", FieldType.SINGLE_SELECT,
                 select_options=["In Stock", "Out Of Stock", "Low Stock", "Unknown"]),
        FieldDef("Stock Quantity", FieldType.NUMBER),
        FieldDef("Variants Count", FieldType.NUMBER, required=True),
        FieldDef("Main Image URL", FieldType.URL),
        FieldDef("Image URLs", FieldType.LONG_TEXT),
        FieldDef("Ships From", FieldType.TEXT),
        FieldDef("Shipping Min Days", FieldType.NUMBER),
        FieldDef("Shipping Max Days", FieldType.NUMBER),
        FieldDef("Syncee Added At", FieldType.DATE),
        FieldDef("Syncee Updated At", FieldType.DATE),
        FieldDef("First Seen At", FieldType.DATE, required=True),
        FieldDef("Last Seen At", FieldType.DATE, required=True),
        FieldDef("Last Changed At", FieldType.DATE),
        FieldDef("Enriched At", FieldType.DATE),  # set on detail fetch → enables chunked enrich
        FieldDef("Active", FieldType.BOOLEAN, required=True),
        FieldDef("Is New", FieldType.BOOLEAN, required=True),
        FieldDef("Supplier Eligible", FieldType.BOOLEAN, required=True),
        FieldDef(
            "Product Gate Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(HardGateStatus),
        ),
        FieldDef("Product Score", FieldType.NUMBER, number_decimals=1),
        FieldDef("Product Score Version", FieldType.TEXT),
        FieldDef(
            "Collection", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(Collection),
        ),
        FieldDef("Classification Confidence", FieldType.NUMBER, number_decimals=2),
        FieldDef(
            "Review Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(ProductReviewStatus),
        ),
        FieldDef(
            "Selection Status", FieldType.SINGLE_SELECT, required=True,
            select_options=_opts(SelectionStatus),
        ),
        FieldDef("Exclusion Reason Codes", FieldType.LONG_TEXT),
        FieldDef("Risk Flags", FieldType.LONG_TEXT),
        FieldDef("Content Angle", FieldType.LONG_TEXT),
        FieldDef("Manual Notes", FieldType.LONG_TEXT),
        FieldDef("Record Fingerprint", FieldType.TEXT, required=True),
        FieldDef("Raw Data", FieldType.LONG_TEXT),
        FieldDef("Last Scan Run", FieldType.LINK_ROW, link_table=T_SCAN_RUNS),
        # --- Publish-prep (approved → Shopify-ready) -------------------------------
        FieldDef("Cleaned Title", FieldType.TEXT),
        FieldDef("Description HTML", FieldType.LONG_TEXT),
        FieldDef("Product Type", FieldType.TEXT),
        FieldDef("Vendor", FieldType.TEXT),
        FieldDef("Material", FieldType.TEXT),
        FieldDef("Dimensions", FieldType.TEXT),
        FieldDef("Weight", FieldType.TEXT),
        FieldDef("Publish Tags", FieldType.LONG_TEXT),
        FieldDef("SEO Title", FieldType.TEXT),
        FieldDef("Meta Description", FieldType.LONG_TEXT),
        FieldDef("Handle", FieldType.TEXT),
        FieldDef("Image Alt Text", FieldType.LONG_TEXT),
        FieldDef("Original Image URL", FieldType.URL),
        FieldDef("Processed Image", FieldType.FILE),
        FieldDef("Image QA", FieldType.LONG_TEXT),
        FieldDef("Content Version", FieldType.TEXT),
        FieldDef("Shopify Product ID", FieldType.TEXT),
        FieldDef(
            "Publish-Prep Status", FieldType.SINGLE_SELECT,
            select_options=_opts(PublishPrepStatus),
        ),
    ],
)


# --- Scan Runs (spec §12.2) --------------------------------------------------------

SCAN_RUNS = TableDef(
    T_SCAN_RUNS,
    [
        FieldDef("Run ID", FieldType.TEXT, required=True, primary=True),
        FieldDef("Run Type", FieldType.SINGLE_SELECT, select_options=_opts(RunType)),
        FieldDef("Status", FieldType.SINGLE_SELECT, select_options=_opts(RunStatus)),
        FieldDef("Started At", FieldType.DATE),
        FieldDef("Completed At", FieldType.DATE),
        FieldDef("Category", FieldType.TEXT),
        FieldDef("Products Seen", FieldType.NUMBER),
        FieldDef("Products Created", FieldType.NUMBER),
        FieldDef("Products Updated", FieldType.NUMBER),
        FieldDef("Products Unchanged", FieldType.NUMBER),
        FieldDef("Products Failed", FieldType.NUMBER),
        FieldDef("Suppliers Created", FieldType.NUMBER),
        FieldDef("Suppliers Updated", FieldType.NUMBER),
        FieldDef("Suppliers Unchanged", FieldType.NUMBER),
        FieldDef("Pages Processed", FieldType.NUMBER),
        FieldDef("Last Page", FieldType.NUMBER),
        FieldDef("Last Cursor", FieldType.TEXT),
        FieldDef("Last Product Key", FieldType.TEXT),
        FieldDef("Checkpoint Data", FieldType.LONG_TEXT),
        FieldDef("Error Summary", FieldType.LONG_TEXT),
        FieldDef("Configuration Hash", FieldType.TEXT),
        FieldDef("Scanner Version", FieldType.TEXT),
        FieldDef(
            "Completeness Status", FieldType.SINGLE_SELECT,
            select_options=_opts(CompletenessStatus),
        ),
        FieldDef("Notes", FieldType.LONG_TEXT),
    ],
)


# --- Product Changes (spec §13) ----------------------------------------------------

PRODUCT_CHANGES = TableDef(
    T_PRODUCT_CHANGES,
    [
        FieldDef("Change ID", FieldType.TEXT, required=True, primary=True),
        FieldDef("Product", FieldType.LINK_ROW, link_table=T_PRODUCTS),
        FieldDef("Scan Run", FieldType.LINK_ROW, link_table=T_SCAN_RUNS),
        FieldDef("Detected At", FieldType.DATE),
        FieldDef("Changed Fields", FieldType.LONG_TEXT),
        FieldDef("Previous Values", FieldType.LONG_TEXT),
        FieldDef("New Values", FieldType.LONG_TEXT),
        FieldDef(
            "Change Type", FieldType.SINGLE_SELECT,
            select_options=[
                "Price Changed", "Shipping Changed", "Stock Changed", "Content Changed",
                "Supplier Changed", "Availability Changed", "Multiple Changes",
            ],
        ),
    ],
)


# --- Manual Decisions (spec §14.2) -------------------------------------------------

MANUAL_DECISIONS = TableDef(
    T_MANUAL_DECISIONS,
    [
        FieldDef("Decision ID", FieldType.TEXT, required=True, primary=True),
        FieldDef("Entity Type", FieldType.SINGLE_SELECT, select_options=_opts(EntityType)),
        FieldDef("Supplier", FieldType.LINK_ROW, link_table=T_SUPPLIERS),
        FieldDef("Product", FieldType.LINK_ROW, link_table=T_PRODUCTS),
        FieldDef("Previous Status", FieldType.TEXT),
        FieldDef("New Status", FieldType.TEXT),
        FieldDef("Decision", FieldType.SINGLE_SELECT, select_options=_opts(DecisionValue)),
        FieldDef("Reason", FieldType.LONG_TEXT),
        FieldDef("Decided At", FieldType.DATE),
        FieldDef("Decided By", FieldType.TEXT),
    ],
)


# --- Selection Batches (spec §15.2) ------------------------------------------------

SELECTION_BATCHES = TableDef(
    T_SELECTION_BATCHES,
    [
        FieldDef("Batch ID", FieldType.TEXT, required=True, primary=True),
        FieldDef("Batch Type", FieldType.SINGLE_SELECT, select_options=_opts(BatchType)),
        FieldDef("Status", FieldType.SINGLE_SELECT, select_options=_opts(BatchStatus)),
        FieldDef("Created At", FieldType.DATE),
        FieldDef("Planned Publication Date", FieldType.DATE),
        FieldDef("Products", FieldType.LINK_ROW, link_table=T_PRODUCTS),
        FieldDef("Product Count", FieldType.NUMBER),
        FieldDef("Kitchen Convenience Count", FieldType.NUMBER),
        FieldDef("Home Comfort Count", FieldType.NUMBER),
        FieldDef("Practical Finds Count", FieldType.NUMBER),
        FieldDef("Notes", FieldType.LONG_TEXT),
    ],
)


# Ordered so that link targets (Scan Runs, Suppliers, Products) are created before the
# tables that link to them.
ALL_TABLES: list[TableDef] = [
    SCAN_RUNS,
    SUPPLIERS,
    PRODUCTS,
    PRODUCT_CHANGES,
    MANUAL_DECISIONS,
    SELECTION_BATCHES,
]

# Maps table name -> the env var holding its Baserow table ID (spec §16.1).
TABLE_ENV_VARS: dict[str, str] = {
    T_SUPPLIERS: "BASEROW_SUPPLIERS_TABLE_ID",
    T_PRODUCTS: "BASEROW_PRODUCTS_TABLE_ID",
    T_SCAN_RUNS: "BASEROW_SCAN_RUNS_TABLE_ID",
    T_PRODUCT_CHANGES: "BASEROW_PRODUCT_CHANGES_TABLE_ID",
    T_MANUAL_DECISIONS: "BASEROW_MANUAL_DECISIONS_TABLE_ID",
    T_SELECTION_BATCHES: "BASEROW_SELECTION_BATCHES_TABLE_ID",
}
