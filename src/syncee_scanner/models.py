"""Shared domain enums and normalized record models.

These status vocabularies come straight from the spec (§10.3, §11.3–§11.6, §12.3–§12.5,
§13, §14.3, §15.3–§15.4) and are the single source of truth for the strings the scanner
writes to Baserow single-select fields. Baserow only *displays* them; logic lives here.
"""

from __future__ import annotations

from enum import Enum


class SupplierEligibility(str, Enum):
    """Suppliers.Eligibility Status (spec §10.3)."""

    UNSCORED = "Unscored"
    GATE_FAILED = "Gate Failed"
    SCORED_REJECTED = "Scored Rejected"
    MANUAL_REVIEW = "Manual Review"
    APPROVED = "Approved"
    MANUALLY_APPROVED = "Manually Approved"
    MANUALLY_BLOCKED = "Manually Blocked"
    INACTIVE = "Inactive"


class HardGateStatus(str, Enum):
    """Generic hard-gate status for suppliers and products."""

    UNSCORED = "Unscored"
    PASSED = "Passed"
    FAILED = "Failed"
    EXCLUDED_BY_SUPPLIER = "Excluded by Supplier"


class ManualOverride(str, Enum):
    """Suppliers.Manual Override (spec §10.4)."""

    NONE = "None"
    APPROVE = "Approve"
    BLOCK = "Block"


class Collection(str, Enum):
    """Products.Collection (spec §11.3)."""

    KITCHEN_CONVENIENCE = "Kitchen Convenience"
    KITCHEN_UTENSILS = "Kitchen Utensils"  # reserved (unused — EU utensil supply too thin)
    DINING = "Dining"
    HOME_COMFORT = "Home Comfort"
    BATHROOM = "Bathroom"
    PRACTICAL_FINDS = "Practical Finds"  # legacy catch-all; retired as a target line
    UNCLASSIFIED = "Unclassified"


class ProductReviewStatus(str, Enum):
    """Products.Review Status (spec §11.4)."""

    UNSCORED = "Unscored"
    EXCLUDED_BY_SUPPLIER = "Excluded by Supplier"
    GATE_FAILED = "Gate Failed"
    SCORED_REJECTED = "Scored Rejected"
    MANUAL_REVIEW = "Manual Review"
    SHORTLISTED = "Shortlisted"
    APPROVED = "Approved"
    MANUALLY_REJECTED = "Manually Rejected"


class SelectionStatus(str, Enum):
    """Products.Selection Status (spec §11.5)."""

    NOT_SELECTED = "Not Selected"
    INITIAL_ASSORTMENT_CANDIDATE = "Initial Assortment Candidate"
    INITIAL_ASSORTMENT_SELECTED = "Initial Assortment Selected"
    NEW_ARRIVAL_CANDIDATE = "New Arrival Candidate"
    NEW_ARRIVAL_SELECTED = "New Arrival Selected"
    PUBLISHED = "Published"
    ARCHIVED = "Archived"


class PublishPrepStatus(str, Enum):
    """Products.Publish-Prep Status — the approved → Shopify-ready workflow."""

    NOT_STARTED = "Not Started"
    CONTENT_READY = "Content Ready"          # normalized fields + SEO copy generated
    IMAGES_READY = "Images Ready"            # images transformed + finished + QA-passed
    READY_TO_PUBLISH = "Ready to Publish"    # content + images signed off
    NEEDS_ATTENTION = "Needs Attention"      # QA flagged (e.g. low-res source, altered product)
    PUSHED = "Pushed to Shopify"


class MarginStatus(str, Enum):
    """Products.Margin Status (spec §11.6)."""

    UNKNOWN = "Unknown"
    INCOMPLETE = "Incomplete"
    CALCULATED = "Calculated"
    BELOW_MINIMUM = "Below Minimum"
    ACCEPTABLE = "Acceptable"
    TARGET_MET = "Target Met"


class RunType(str, Enum):
    """Scan Runs.Run Type (spec §12.3)."""

    DISCOVERY = "Discovery"
    FULL_SCAN = "Full Scan"
    INCREMENTAL_SCAN = "Incremental Scan"
    RECONCILIATION = "Reconciliation"
    SUPPLIER_SCORING = "Supplier Scoring"
    PRODUCT_SCORING = "Product Scoring"
    INITIAL_SELECTION = "Initial Selection"
    NEW_ARRIVALS_SELECTION = "New Arrivals Selection"


class RunStatus(str, Enum):
    """Scan Runs.Status (spec §12.4)."""

    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    COMPLETED_WITH_ERRORS = "Completed With Errors"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class CompletenessStatus(str, Enum):
    """Scan Runs.Completeness Status (spec §12.5)."""

    UNKNOWN = "Unknown"
    PARTIAL = "Partial"
    COMPLETE = "Complete"
    COMPLETE_WITH_KNOWN_LIMITATIONS = "Complete With Known Limitations"
    UNVERIFIED = "Unverified"


class EntityType(str, Enum):
    """Manual Decisions.Entity Type."""

    SUPPLIER = "Supplier"
    PRODUCT = "Product"


class DecisionValue(str, Enum):
    """Manual Decisions.Decision (spec §14.3)."""

    APPROVE = "Approve"
    REJECT = "Reject"
    BLOCK = "Block"
    RESTORE = "Restore"
    SELECT = "Select"
    REMOVE_FROM_SELECTION = "Remove From Selection"
    PUBLISH = "Publish"
    ARCHIVE = "Archive"


class BatchType(str, Enum):
    """Selection Batches.Batch Type (spec §15.3)."""

    INITIAL_ASSORTMENT = "Initial Assortment"
    NEW_ARRIVALS = "New Arrivals"


class BatchStatus(str, Enum):
    """Selection Batches.Status (spec §15.4)."""

    DRAFT = "Draft"
    UNDER_REVIEW = "Under Review"
    APPROVED = "Approved"
    PUBLISHED = "Published"
    CANCELLED = "Cancelled"
