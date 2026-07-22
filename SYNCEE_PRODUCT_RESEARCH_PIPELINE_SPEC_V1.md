# Syncee Product Research Pipeline Specification

**Version:** 1.0  
**Project:** RB Home Relaunch  
**Document type:** Functional and technical specification  
**Primary storage:** Baserow  
**Execution model:** Python + Playwright + Baserow API  
**Status:** Ready for implementation planning

---

## 1. Purpose

Build a reliable product research pipeline that scans all products available to the authenticated Syncee account within the Home & Kitchen category, identifies unique suppliers, scores suppliers first, excludes products from rejected suppliers, scores the remaining products, classifies them into RB Home collections, and produces:

- an initial assortment of 18–24 products;
- a recurring shortlist of 4 new-arrival products every 1–2 weeks.

The system must support:

- one initial full catalog scan;
- weekly incremental scans for newly added products;
- periodic reconciliation scans;
- manual review and overrides in Baserow;
- resumable processing;
- auditable scoring decisions;
- low-maintenance operation.

The first implementation must be a deterministic research pipeline, not a complex autonomous multi-agent platform.

---

## 2. Business Context

RB Home is a low-attention Shopify business in the Home Comfort / Kitchen Convenience niche.

Primary collections:

1. `Kitchen Convenience`
2. `Home Comfort`
3. `Practical Finds`

Operational constraints:

- the project should require no more than 3–5 hours per week;
- paid ads are not part of the initial scope;
- the catalog should remain small and intentional;
- the system must prioritize low operational complexity;
- products with high safety, compliance, food-contact, refund, or delivery risk must be filtered out unless properly verified;
- no product is published automatically during the prototype phase.

---

## 3. Primary Business Outcomes

The system must enable the following workflow:

```text
Syncee Home & Kitchen Catalog
        ↓
Full Product Scan
        ↓
Product Normalization and Deduplication
        ↓
Unique Supplier Extraction
        ↓
Supplier Hard Gates
        ↓
Supplier Weighted Scoring
        ↓
Reject Suppliers Below Threshold
        ↓
Automatically Exclude Their Products
        ↓
Product Hard Gates
        ↓
Product Weighted Scoring
        ↓
Collection Classification
        ↓
Initial Assortment Shortlist
        ↓
Manual Review in Baserow
        ↓
18–24 Initial Products
        ↓
Weekly Incremental Scan
        ↓
New Supplier and Product Scoring
        ↓
4 New Arrival Candidates
        ↓
Manual Approval
        ↓
Publish Every 1–2 Weeks
```

---

## 4. Scope

### 4.1 Included

The system must include:

- manual initial Syncee authentication;
- persistent browser session;
- headless browser execution;
- Syncee marketplace discovery;
- full scan of accessible Home & Kitchen products;
- supplier discovery and deduplication;
- product discovery and deduplication;
- Baserow persistence;
- Baserow relational links;
- supplier hard gates;
- supplier weighted scoring;
- supplier manual overrides;
- automatic product exclusion based on supplier status;
- product hard gates;
- product weighted scoring;
- product collection classification;
- initial assortment selection;
- weekly incremental scan;
- detection of new products;
- detection of changed products;
- handling of newly discovered suppliers;
- periodic reconciliation scan;
- audit trail;
- structured logging;
- resumable runs;
- CLI commands;
- unit and integration tests;
- optional CSV exports;
- optional later scheduling through Windmill.

### 4.2 Excluded

The system must not include in version 1:

- automatic Shopify import;
- automatic Shopify publication;
- automatic supplier contact;
- automatic approval requests to suppliers;
- ordering or payment automation;
- browser fingerprint evasion;
- CAPTCHA bypass;
- proxy rotation;
- bypassing Syncee access restrictions;
- scraping data unavailable to the authenticated account;
- a custom web dashboard;
- a vector database;
- a general-purpose agent orchestration platform;
- heavy LLM usage for every product;
- advanced machine learning;
- paid-ad campaign logic;
- customer support automation.

---

## 5. Key Architectural Decisions

### 5.1 Core stack

Use:

- Python 3.12+
- Playwright
- Chromium
- Pydantic
- HTTP client such as `httpx`
- YAML configuration
- Baserow REST API
- pytest
- structured logging
- optional Typer for CLI

### 5.2 Persistent storage

Baserow is the primary and only persistent operational data store.

Baserow must store:

- suppliers;
- products;
- scan runs;
- scan checkpoints;
- product changes;
- manual decisions;
- selection batches;
- scores;
- classifications;
- current review and selection statuses.

SQLite must not be introduced.

CSV is optional and only for:

- backup;
- sharing;
- offline analysis;
- migration;
- external review.

CSV must not be required for:

- deduplication;
- scoring;
- incremental scan logic;
- resume;
- source-of-truth state.

### 5.3 Browser automation

Use Playwright with:

- headed mode for login and discovery;
- headless mode for scheduled and normal scans;
- saved storage state;
- low concurrency;
- configurable delays;
- screenshot and HTML capture on failure.

### 5.4 Extraction priority

Use the following extraction order:

1. structured API responses used by Syncee UI;
2. XHR or GraphQL response interception;
3. embedded structured page data;
4. DOM extraction as fallback.

The implementation must not rely exclusively on fragile CSS selectors when stable IDs or structured JSON are available.

### 5.5 Scoring ownership

All scoring logic must live in application code and configuration.

Baserow may display stored scores, statuses, and reasons, but scoring logic must not depend on Baserow formulas.

This ensures:

- version control;
- testability;
- deterministic results;
- auditability;
- easier rule changes.

---

## 6. Operating Modes

The system must support four core processing modes.

### 6.1 Discovery

Purpose:

- inspect Syncee page structure;
- identify routes;
- identify product and supplier IDs;
- identify pagination;
- identify sorting options;
- identify available date fields;
- identify API responses;
- capture sample responses.

### 6.2 Full Scan

Purpose:

- scan all accessible Home & Kitchen products;
- collect all suppliers and products;
- establish the initial Baserow dataset;
- score suppliers;
- score eligible products;
- classify products;
- create the initial assortment shortlist.

### 6.3 Incremental Scan

Purpose:

- detect products added after the previous successful incremental scan;
- detect newly discovered suppliers;
- detect changes to known products;
- score only new or changed entities when possible;
- create new-arrival candidates.

### 6.4 Reconciliation Scan

Purpose:

- verify whether previously known products and suppliers still exist;
- detect unavailable or inactive products;
- refresh shipping, price, and stock data;
- detect stale records;
- correct missed incremental changes.

Recommended cadence:

- incremental scan: weekly;
- reconciliation scan: every 4–8 weeks.

---

## 7. Authentication Requirements

### 7.1 Initial login command

```bash
syncee-scanner auth login
```

Expected behavior:

1. launch Chromium in headed mode;
2. open Syncee;
3. allow the user to log in manually;
4. wait until authenticated marketplace content is visible;
5. save Playwright storage state;
6. verify marketplace access;
7. close the browser.

Default session location:

```text
data/auth/storage_state.json
```

The session file must:

- be excluded from version control;
- not be logged;
- not be uploaded to Baserow;
- not contain manually entered plaintext credentials outside browser-managed session state.

### 7.2 Session validation

Command:

```bash
syncee-scanner auth validate
```

Before every scan:

1. load saved session;
2. open a known authenticated Syncee page;
3. verify that authenticated marketplace content is available;
4. detect login redirects;
5. detect access denial;
6. fail with a clear error when the session has expired.

Required error code:

```text
AUTH_SESSION_EXPIRED
```

---

## 8. Syncee Discovery Phase

### 8.1 Discovery command

```bash
syncee-scanner discover
```

### 8.2 Discovery objectives

The discovery utility must determine:

- category URL structure;
- Home & Kitchen category hierarchy;
- product search URL structure;
- supplier page URL structure;
- product detail page structure;
- supplier detail page structure;
- stable product IDs;
- stable supplier IDs;
- pagination mechanism;
- cursor behavior;
- infinite scroll behavior;
- sort options;
- newest-first sorting availability;
- product-added timestamp availability;
- product-updated timestamp availability;
- available product filters;
- available supplier filters;
- API endpoints used by the UI;
- GraphQL operations if present;
- XHR response structures;
- shipping-data location;
- stock-data location;
- approval-status location;
- image-data location;
- variant-data location;
- category and subcategory fields;
- supplier country and destination fields.

### 8.3 Discovery outputs

```text
artifacts/discovery/
  routes.json
  fields.json
  pagination.json
  sort_options.json
  network_endpoints.json
  sample_product_list_response.json
  sample_product_detail_response.json
  sample_supplier_response.json
  screenshots/
  discovery_report.md
```

### 8.4 Discovery gate

The coding agent must not build the final full scanner until discovery confirms:

- a stable product identity;
- a stable supplier identity;
- a viable pagination or cursor strategy;
- a viable extraction method;
- whether incremental newest-first scanning is possible.

---

## 9. Baserow Data Model

Create one Baserow database:

```text
RB Home Product Research
```

Required tables:

1. `Suppliers`
2. `Products`
3. `Scan Runs`
4. `Product Changes`
5. `Manual Decisions`
6. `Selection Batches`

No extra tables should be created unless required by implementation evidence.

---

## 10. Suppliers Table

### 10.1 Primary key strategy

Create a stable application-level field:

```text
Supplier Key
```

Priority:

1. Syncee supplier ID;
2. normalized supplier URL;
3. deterministic hash of normalized supplier name and country.

Supplier name alone must not be considered unique.

### 10.2 Required fields

| Field | Baserow type | Required |
|---|---|---:|
| Supplier Key | Single line text | Yes |
| Syncee Supplier ID | Single line text | No |
| Supplier Name | Single line text | Yes |
| Supplier URL | URL | Yes |
| Location Country | Single line text or single select | No |
| Dispatch Countries | Long text or multiple select | No |
| Ships To Countries | Long text or multiple select | No |
| Approval Required | Boolean | No |
| Supplier Rating | Number | No |
| Review Count | Number | No |
| Catalog Product Count | Number | No |
| Relevant Product Count | Number | Yes |
| Shipping Min Days | Number | No |
| Shipping Max Days | Number | No |
| Shipping Policy Available | Boolean | No |
| Return Policy Available | Boolean | No |
| Contact Information Available | Boolean | No |
| Data Completeness % | Number | Yes |
| First Seen At | Date and time | Yes |
| Last Seen At | Date and time | Yes |
| Last Changed At | Date and time | No |
| Active | Boolean | Yes |
| Hard Gate Status | Single select | Yes |
| Supplier Score | Number | No |
| Supplier Score Version | Single line text | No |
| Eligibility Status | Single select | Yes |
| Reason Codes | Long text | No |
| Manual Override | Single select | No |
| Manual Notes | Long text | No |
| Raw Data | Long text | No |
| Last Scan Run | Link to Scan Runs | No |

### 10.3 Supplier eligibility statuses

```text
Unscored
Gate Failed
Scored Rejected
Manual Review
Approved
Manually Approved
Manually Blocked
Inactive
```

### 10.4 Manual override values

```text
None
Approve
Block
```

---

## 11. Products Table

### 11.1 Primary key strategy

Create a stable application-level field:

```text
Product Key
```

Priority:

1. Syncee product ID;
2. supplier key + supplier SKU;
3. normalized canonical product URL;
4. deterministic hash of supplier key + normalized name + variant signature.

Product name alone must never be treated as unique.

### 11.2 Required fields

| Field | Baserow type | Required |
|---|---|---:|
| Product Key | Single line text | Yes |
| Syncee Product ID | Single line text | No |
| Product Name | Single line text | Yes |
| Product URL | URL | Yes |
| Supplier | Link to Suppliers | Yes |
| Supplier SKU | Single line text | No |
| Brand | Single line text | No |
| Syncee Category | Single line text | No |
| Syncee Subcategory | Single line text | No |
| Description | Long text | No |
| Currency | Single line text or single select | No |
| Supplier Price | Number | No |
| Suggested Retail Price | Number | No |
| Proposed Retail Price | Number | No |
| Shipping Cost | Number | No |
| Shipping Cost Known | Boolean | Yes |
| Estimated Landed Cost | Number | No |
| Estimated Margin Amount | Number | No |
| Estimated Margin % | Number | No |
| Margin Status | Single select | Yes |
| Stock Status | Single select | No |
| Stock Quantity | Number | No |
| Variants Count | Number | Yes |
| Main Image URL | URL | No |
| Image URLs | Long text | No |
| Ships From | Single line text | No |
| Shipping Min Days | Number | No |
| Shipping Max Days | Number | No |
| Syncee Added At | Date and time | No |
| Syncee Updated At | Date and time | No |
| First Seen At | Date and time | Yes |
| Last Seen At | Date and time | Yes |
| Last Changed At | Date and time | No |
| Active | Boolean | Yes |
| Is New | Boolean | Yes |
| Supplier Eligible | Boolean | Yes |
| Product Gate Status | Single select | Yes |
| Product Score | Number | No |
| Product Score Version | Single line text | No |
| Collection | Single select | Yes |
| Classification Confidence | Number | No |
| Review Status | Single select | Yes |
| Selection Status | Single select | Yes |
| Exclusion Reason Codes | Long text | No |
| Risk Flags | Long text | No |
| Content Angle | Long text | No |
| Manual Notes | Long text | No |
| Record Fingerprint | Single line text | Yes |
| Raw Data | Long text | No |
| Last Scan Run | Link to Scan Runs | No |

### 11.3 Collection values

```text
Kitchen Convenience
Home Comfort
Practical Finds
Unclassified
```

### 11.4 Product review statuses

```text
Unscored
Excluded by Supplier
Gate Failed
Scored Rejected
Manual Review
Shortlisted
Approved
Manually Rejected
```

### 11.5 Selection statuses

```text
Not Selected
Initial Assortment Candidate
Initial Assortment Selected
New Arrival Candidate
New Arrival Selected
Published
Archived
```

### 11.6 Margin statuses

```text
Unknown
Incomplete
Calculated
Below Minimum
Acceptable
Target Met
```

---

## 12. Scan Runs Table

### 12.1 Purpose

Store:

- run metadata;
- progress;
- checkpoints;
- completion status;
- errors;
- counts;
- scanner version;
- configuration fingerprint.

### 12.2 Required fields

| Field | Baserow type |
|---|---|
| Run ID | Single line text |
| Run Type | Single select |
| Status | Single select |
| Started At | Date and time |
| Completed At | Date and time |
| Category | Single line text |
| Products Seen | Number |
| Products Created | Number |
| Products Updated | Number |
| Products Unchanged | Number |
| Products Failed | Number |
| Suppliers Created | Number |
| Suppliers Updated | Number |
| Suppliers Unchanged | Number |
| Pages Processed | Number |
| Last Page | Number |
| Last Cursor | Single line text |
| Last Product Key | Single line text |
| Checkpoint Data | Long text |
| Error Summary | Long text |
| Configuration Hash | Single line text |
| Scanner Version | Single line text |
| Completeness Status | Single select |
| Notes | Long text |

### 12.3 Run types

```text
Discovery
Full Scan
Incremental Scan
Reconciliation
Supplier Scoring
Product Scoring
Initial Selection
New Arrivals Selection
```

### 12.4 Run statuses

```text
Pending
Running
Paused
Completed
Completed With Errors
Failed
Cancelled
```

### 12.5 Completeness statuses

```text
Unknown
Partial
Complete
Complete With Known Limitations
Unverified
```

---

## 13. Product Changes Table

Create one row when a tracked product field changes.

Required fields:

| Field | Baserow type |
|---|---|
| Product | Link to Products |
| Scan Run | Link to Scan Runs |
| Detected At | Date and time |
| Changed Fields | Long text |
| Previous Values | Long text |
| New Values | Long text |
| Change Type | Single select |

Change types:

```text
Price Changed
Shipping Changed
Stock Changed
Content Changed
Supplier Changed
Availability Changed
Multiple Changes
```

Tracked fields should include:

- supplier price;
- suggested retail price;
- shipping cost;
- shipping time;
- stock status;
- stock quantity;
- title;
- description;
- images;
- variants;
- supplier link;
- active status;
- category;
- source timestamps.

---

## 14. Manual Decisions Table

### 14.1 Purpose

Provide an immutable audit trail for manual status changes.

### 14.2 Required fields

| Field | Baserow type |
|---|---|
| Decision ID | Single line text |
| Entity Type | Single select |
| Supplier | Link to Suppliers |
| Product | Link to Products |
| Previous Status | Single line text |
| New Status | Single line text |
| Decision | Single select |
| Reason | Long text |
| Decided At | Date and time |
| Decided By | Single line text |

### 14.3 Decision values

```text
Approve
Reject
Block
Restore
Select
Remove From Selection
Publish
Archive
```

The current status may be stored on the supplier or product row, but every manual change must also create a Manual Decisions row.

---

## 15. Selection Batches Table

### 15.1 Purpose

Represent:

- the initial assortment;
- later new-arrival releases.

### 15.2 Required fields

| Field | Baserow type |
|---|---|
| Batch ID | Single line text |
| Batch Type | Single select |
| Status | Single select |
| Created At | Date and time |
| Planned Publication Date | Date |
| Products | Link to Products |
| Product Count | Number |
| Kitchen Convenience Count | Number |
| Home Comfort Count | Number |
| Practical Finds Count | Number |
| Notes | Long text |

### 15.3 Batch types

```text
Initial Assortment
New Arrivals
```

### 15.4 Batch statuses

```text
Draft
Under Review
Approved
Published
Cancelled
```

---

## 16. Baserow API Integration

### 16.1 Required environment variables

```text
BASEROW_API_URL
BASEROW_DATABASE_TOKEN
BASEROW_SUPPLIERS_TABLE_ID
BASEROW_PRODUCTS_TABLE_ID
BASEROW_SCAN_RUNS_TABLE_ID
BASEROW_PRODUCT_CHANGES_TABLE_ID
BASEROW_MANUAL_DECISIONS_TABLE_ID
BASEROW_SELECTION_BATCHES_TABLE_ID
```

Secrets must not be committed to the repository.

### 16.2 Field references

Prefer Baserow field IDs internally when possible so visible column renaming does not break the scanner.

### 16.3 Batch settings

Default configuration:

```yaml
baserow:
  create_batch_size: 100
  update_batch_size: 100
  request_concurrency: 2
  max_retries: 3
  retry_backoff_seconds: 2
```

All values must be configurable.

### 16.4 Upsert strategy

At the beginning of a run, load lightweight indexes:

```text
supplier_key → Baserow supplier row ID
product_key → Baserow product row ID
```

The scanner must not query Baserow separately for every product.

Processing sequence:

1. normalize extracted records;
2. generate supplier and product keys;
3. match keys against in-memory index;
4. separate records into:
   - new suppliers;
   - changed suppliers;
   - unchanged suppliers;
   - new products;
   - changed products;
   - unchanged products;
5. batch-create new rows;
6. batch-update changed rows;
7. refresh Last Seen At when needed;
8. update scan checkpoint;
9. continue.

### 16.5 Idempotency

Repeated processing of the same Syncee page must not create duplicate suppliers or products.

---

## 17. Full Scan Requirements

### 17.1 Command

```bash
syncee-scanner scan full --category "Home & Kitchen"
```

### 17.2 Meaning of full scan

A full scan must attempt to collect all Home & Kitchen products visible to the authenticated Syncee account and plan.

The system must not claim to have scanned products that are inaccessible because of:

- account permissions;
- plan restrictions;
- supplier restrictions;
- marketplace access restrictions.

### 17.3 Full scan flow

1. validate session;
2. create Scan Runs row;
3. open target category;
4. apply configured filters;
5. iterate through all pages or cursors;
6. extract product summaries;
7. collect or resolve supplier identities;
8. collect required product details;
9. collect required supplier details;
10. normalize records;
11. deduplicate;
12. persist in batches;
13. checkpoint after each successful page or batch;
14. continue until complete;
15. mark missing or failed items;
16. complete run summary.

### 17.4 Pagination termination

Stop only when one of the following is verified:

- final page reached;
- no next cursor;
- UI confirms no more results;
- API confirms no more results;
- configured safety limit reached.

If the same page or cursor repeats unexpectedly, raise:

```text
PAGINATION_LOOP_DETECTED
```

### 17.5 Resume support

Command:

```bash
syncee-scanner scan resume <run_id>
```

Checkpoint data must include:

```json
{
  "page": 12,
  "cursor": "next_cursor_value",
  "last_product_key": "product-key",
  "products_processed": 2400,
  "suppliers_processed": 173,
  "updated_at": "ISO-8601 timestamp"
}
```

Resume flow:

1. load Scan Runs row;
2. restore checkpoint;
3. reload Baserow indexes;
4. continue from next safe page or cursor;
5. rely on idempotent upsert logic.

### 17.6 Incremental persistence

Do not hold the full catalog in memory.

Default:

```yaml
persistence:
  batch_size: 100
  checkpoint_every_products: 250
```

---

## 18. Product Normalization

Normalize before persistence.

### 18.1 Text normalization

- trim whitespace;
- collapse repeated whitespace;
- preserve original text in Raw Data;
- normalize Unicode;
- avoid destructive translation during ingestion.

### 18.2 URL normalization

- remove tracking parameters;
- normalize trailing slashes;
- preserve canonical route;
- retain original URL in Raw Data if different.

### 18.3 Country normalization

Normalize country names to consistent values.

### 18.4 Price normalization

- parse decimal separators;
- retain currency;
- do not convert currency unless an explicit conversion service is added later;
- mark missing or invalid values.

### 18.5 Date normalization

Store ISO-8601-compatible timestamps.

### 18.6 Boolean normalization

Convert source labels consistently into true, false, or unknown.

---

## 19. Deduplication Rules

### 19.1 Supplier deduplication

Merge when:

- source supplier ID matches;
- canonical supplier URL matches;
- or a deterministic normalized identity is confirmed.

Do not merge solely because names are similar.

### 19.2 Product deduplication

Deduplicate by stable source identity.

Near-identical products from different suppliers must remain separate because:

- price differs;
- shipping differs;
- quality differs;
- stock differs;
- refund risk differs.

### 19.3 Record fingerprints

For every product and supplier, calculate a deterministic fingerprint of tracked normalized fields.

On repeated observation:

1. compare fingerprint;
2. update Last Seen At;
3. update Last Changed At only when fingerprint differs;
4. create Product Changes record when product data changed;
5. persist new fingerprint.

---

## 20. Supplier Scoring

Supplier scoring must run before product scoring.

### 20.1 Command

```bash
syncee-scanner score suppliers
```

### 20.2 Supplier hard gates

Default hard gates:

1. supplier ships to at least one target market;
2. supplier shipping time does not exceed configured maximum;
3. supplier has at least one active relevant product;
4. supplier identity is sufficiently complete;
5. supplier is not manually blocked;
6. required shipping information is available when configured;
7. supplier is not clearly incompatible with RB Home operational constraints.

### 20.3 Target markets

Default:

```yaml
markets:
  target:
    - Spain
    - Portugal
    - France
    - Germany
    - Italy
    - Austria
    - Belgium
    - Netherlands
    - Ireland
```

### 20.4 Supplier hard-gate configuration

```yaml
supplier_gates:
  max_shipping_days: 10
  require_target_market: true
  require_shipping_policy: false
  require_return_policy: false
  minimum_data_completeness_pct: 60
```

### 20.5 Supplier weighted score

Calculate normalized score from 0 to 100.

Default weights:

| Criterion | Weight |
|---|---:|
| Target-market coverage | 20 |
| Shipping speed | 20 |
| Dispatch proximity | 15 |
| Data completeness | 10 |
| Shipping-policy clarity | 10 |
| Return-policy clarity | 10 |
| Supplier rating and reviews | 5 |
| Relevant catalog depth | 5 |
| Approval friction | 5 |

Weights must sum to 100.

### 20.6 Thresholds

```yaml
supplier_scoring:
  reject_below: 60
  manual_review_from: 60
  approve_from: 75
```

### 20.7 Supplier reason codes

Examples:

```text
NO_TARGET_MARKET
SHIPPING_TOO_SLOW
SHIPPING_UNKNOWN
LOW_DATA_COMPLETENESS
NO_RETURN_POLICY
NO_SHIPPING_POLICY
LOW_SUPPLIER_SCORE
APPROVAL_REQUIRED
MANUALLY_BLOCKED
INSUFFICIENT_RELEVANT_PRODUCTS
```

### 20.8 Manual override

Commands:

```bash
syncee-scanner supplier approve <supplier_key>
syncee-scanner supplier block <supplier_key>
syncee-scanner supplier clear-override <supplier_key>
```

Overrides must:

- update supplier status;
- create Manual Decisions record;
- record previous and new status;
- record timestamp;
- accept optional note.

A failed hard gate cannot be ignored automatically. Only an explicit manual override may change final eligibility.

---

## 21. Supplier-Based Product Exclusion

After supplier scoring:

Products from suppliers with statuses:

```text
Gate Failed
Scored Rejected
Manually Blocked
Inactive
```

must receive:

```text
Supplier Eligible = false
Product Gate Status = Excluded by Supplier
Review Status = Excluded by Supplier
```

These products must:

- remain in Baserow;
- remain available for audit;
- not proceed to product scoring;
- not appear in candidate selection;
- become eligible later if supplier status changes.

---

## 22. Product Hard Gates

Only products from eligible suppliers may proceed.

Default hard gates:

1. supplier is eligible;
2. product belongs to RB Home scope;
3. product is active;
4. product is in stock or otherwise sellable;
5. product has at least one usable image;
6. product has a usable title;
7. supplier price is known;
8. shipping time is within configured limit;
9. margin can be validated or explicitly sent to manual review;
10. product does not present unacceptable compliance risk;
11. product does not present excessive return risk;
12. product is not a prohibited or clearly unsuitable type;
13. product is not a near-duplicate of an already selected concept unless intentionally allowed.

### 22.1 Restricted or high-risk product types

By default, reject or route to manual review:

- electrical products with unclear certification;
- battery-powered products with unclear compliance;
- heating products with unclear safety verification;
- direct food-contact products with unclear material compliance;
- products with medical or health claims;
- fragile glass products with high damage risk;
- products with complex sizing;
- products with likely trademark infringement;
- products with misleading images;
- products with unclear shipping cost where margin cannot be validated;
- products likely to produce high refund rates;
- products with high installation complexity.

---

## 23. Margin Calculation

### 23.1 Landed cost

```text
landed_cost =
    supplier_price
    + shipping_cost
    + estimated_payment_fee
    + estimated_platform_fee
    + expected_return_allowance
```

### 23.2 Gross margin amount

```text
gross_margin_amount =
    proposed_retail_price
    - landed_cost
```

### 23.3 Gross margin percentage

```text
gross_margin_pct =
    gross_margin_amount
    / proposed_retail_price
    × 100
```

### 23.4 Missing values

If shipping cost or another required value is missing:

```text
Margin Status = Incomplete
```

The product must not receive a fully validated margin score.

### 23.5 Default configuration

```yaml
margin:
  minimum_margin_pct: 45
  target_margin_pct: 55
  estimated_payment_fee_pct: 3
  estimated_platform_fee_pct: 2
  expected_return_allowance_pct: 5
```

All values must be configurable.

---

## 24. Product Scoring

### 24.1 Command

```bash
syncee-scanner score products
```

### 24.2 Weighted score

Calculate score from 0 to 100.

Default weights:

| Criterion | Weight |
|---|---:|
| Practical problem solved | 20 |
| Gross margin potential | 20 |
| Shipping simplicity and speed | 15 |
| Visual and content potential | 15 |
| Product differentiation | 10 |
| Low return/refund risk | 10 |
| Data and image quality | 5 |
| Supplier strength | 5 |

### 24.3 Thresholds

```yaml
product_scoring:
  reject_below: 60
  manual_review_from: 60
  shortlist_from: 75
```

### 24.4 Product reason codes

Examples:

```text
SUPPLIER_REJECTED
OUTSIDE_STORE_SCOPE
LOW_MARGIN
MARGIN_UNKNOWN
SHIPPING_TOO_SLOW
SHIPPING_COST_UNKNOWN
HIGH_COMPLIANCE_RISK
HIGH_REFUND_RISK
LOW_CONTENT_POTENTIAL
INSUFFICIENT_IMAGES
MISSING_PRICE
OUT_OF_STOCK
DUPLICATE_CONCEPT
LOW_PRODUCT_SCORE
```

### 24.5 Score versioning

Every scored product must store:

- product score;
- score version;
- input fingerprint;
- reason codes;
- risk flags.

If scoring configuration changes, affected products must be eligible for rescoring.

---

## 25. Collection Classification

Each eligible product must be assigned exactly one primary collection.

### 25.1 Kitchen Convenience

Products that simplify:

- food preparation;
- kitchen organization;
- storage;
- sink routines;
- cleaning;
- countertop routines;
- meal preparation;
- everyday kitchen workflow.

### 25.2 Home Comfort

Products that improve:

- comfort;
- home organization;
- bedroom usability;
- living-room usability;
- lighting comfort;
- relaxation;
- everyday home environment.

Electrical or heating products still require separate compliance checks.

### 25.3 Practical Finds

Products that:

- solve a clear everyday problem;
- do not strongly fit the first two collections;
- are easy to demonstrate;
- have low operational complexity;
- fit RB Home positioning.

### 25.4 Classification strategy

Apply in order:

1. deterministic category mapping;
2. rule-based keyword classification;
3. optional batch LLM classification for uncertain items;
4. manual review below confidence threshold.

Default:

```yaml
classification:
  minimum_confidence: 0.70
```

LLM must not be called for every raw product.

---

## 26. Initial Assortment Selection

### 26.1 Target

Select:

```text
18–24 total products
```

Target balance:

```text
6–8 Kitchen Convenience
6–8 Home Comfort
6–8 Practical Finds
```

### 26.2 Eligibility

Every candidate must:

- have an eligible supplier;
- pass product hard gates;
- meet shortlist threshold;
- have acceptable data quality;
- have acceptable delivery;
- have acceptable margin status;
- have no unresolved critical risk.

### 26.3 Diversity constraints

Default:

- no more than 30% of initial products from one supplier;
- ideally at least two suppliers per collection;
- no more than two near-identical product concepts;
- avoid catalog overconcentration around one narrow problem;
- maintain a mix of price points;
- prioritize evergreen products;
- prioritize products suitable for organic visual content.

### 26.4 Selection score

```text
selection_score =
    product_score
    + collection_balance_adjustment
    + supplier_diversity_adjustment
    + price_point_balance_adjustment
    + content_potential_adjustment
    - duplicate_concept_penalty
```

### 26.5 Command

```bash
syncee-scanner select initial
```

### 26.6 Output

Create:

- Selection Batches row with type `Initial Assortment`;
- product statuses `Initial Assortment Candidate`;
- Baserow view showing all candidates.

No product may become `Initial Assortment Selected` without manual approval.

---

## 27. Incremental Weekly Scan

### 27.1 Command

```bash
syncee-scanner scan incremental
```

### 27.2 Objectives

Detect:

- new products;
- new suppliers;
- changed products;
- inactive products;
- products returned to stock;
- supplier-status changes.

### 27.3 Definition of new product

A product is new when:

```text
Product Key did not exist before the current run
```

Set:

```text
First Seen At = current run timestamp
Is New = true
```

If Syncee exposes a trustworthy added timestamp, store it as Syncee Added At.

### 27.4 Incremental strategy priority

#### Strategy A: source date filter

Use a trusted added-date filter when available.

#### Strategy B: newest-first scanning

1. sort newest first;
2. scan from newest item;
3. stop after a configurable number of consecutive known products or known pages;
4. save unknown product keys.

Default:

```yaml
incremental_scan:
  stop_after_known_products: 200
  stop_after_known_pages: 3
```

#### Strategy C: category snapshot comparison

When no trusted date or stable sorting exists:

1. rescan category index pages;
2. compare current keys with known keys;
3. identify set difference;
4. mark run completeness appropriately.

### 27.5 Completeness rule

If newest-first ordering cannot be verified, the scanner must not claim the incremental scan is complete.

Use:

```text
Completeness Status = Unverified
```

or:

```text
Completeness Status = Complete With Known Limitations
```

### 27.6 New supplier flow

When a new supplier is found:

1. create supplier row;
2. collect required supplier fields;
3. run supplier hard gates;
4. calculate supplier score;
5. determine eligibility;
6. only then score products.

### 27.7 Changed product flow

Only changed products should be rescored unless:

- supplier status changed;
- scoring version changed;
- collection rules changed;
- manual override changed.

---

## 28. Reconciliation Scan

### 28.1 Command

```bash
syncee-scanner scan reconcile
```

### 28.2 Objectives

- verify known products still exist;
- mark missing products inactive;
- refresh price;
- refresh stock;
- refresh shipping;
- refresh supplier status;
- detect stale records;
- detect products missed by incremental logic.

### 28.3 Inactive handling

Do not delete historical rows.

Set:

```text
Active = false
Review Status = Archived or Inactive
```

as appropriate.

---

## 29. New Arrivals Selection

### 29.1 Cadence

Prepare 4 products every 1–2 weeks.

### 29.2 Candidate eligibility

A new-arrival candidate must:

- be newly discovered after the previous accepted cutoff;
- belong to an eligible supplier;
- pass product hard gates;
- meet shortlist threshold;
- not already be selected or published;
- not be too similar to current active catalog;
- have acceptable content potential.

### 29.3 Preferred batch composition

```text
1 Kitchen Convenience
1 Home Comfort
1 Practical Finds
1 highest-value remaining candidate
```

Relax only when a collection has no qualifying candidate.

### 29.4 Diversity constraints

For each batch of four:

- no more than two products from one supplier;
- avoid duplicate concepts;
- avoid all products at one price point;
- prioritize strong organic-content potential;
- prioritize validated delivery and margin.

### 29.5 Command

```bash
syncee-scanner select new-arrivals
```

### 29.6 Output

Create:

- Selection Batches row with type `New Arrivals`;
- linked candidate products;
- product statuses `New Arrival Candidate`;
- planned publication date if provided.

No product may become `New Arrival Selected` without manual approval.

---

## 30. Baserow Views

### 30.1 Supplier views

Create:

```text
Supplier Review
Approved Suppliers
Rejected Suppliers
New Suppliers
Missing Shipping Data
Manual Overrides
Inactive Suppliers
```

### 30.2 Product views

Create:

```text
All Active Products
New Products
Eligible Products
Excluded by Supplier
Product Review
Initial Assortment Candidates
Initial Assortment Selected
New Arrival Candidates
New Arrival Selected
Missing Margin Data
High Risk
Inactive Products
Published Products
```

### 30.3 Scan run views

Create:

```text
Active Runs
Failed Runs
Completed Runs
Completed With Errors
Unverified Completeness
```

Views are for operations only. Business logic must not depend on manually preserved view filters.

---

## 31. AI Usage

AI is optional.

Allowed use cases:

- uncertain collection classification;
- semantic duplicate detection;
- supplier-policy summarization;
- content-potential assessment;
- product scoring explanations;
- content-angle generation.

AI must be used only after deterministic filtering.

### 31.1 AI caching

Cache results using:

- input fingerprint;
- prompt version;
- model identifier.

Do not repeat calls when input and prompt version are unchanged.

### 31.2 AI limits

AI must not:

- override hard gates;
- approve products automatically;
- approve suppliers automatically;
- publish products;
- invent missing shipping or compliance data.

---

## 32. Configuration

Use YAML.

Example:

```yaml
syncee:
  category: "Home & Kitchen"
  headless: true
  browser_timeout_seconds: 60
  page_delay_seconds: 2
  detail_page_delay_seconds: 2
  max_retries: 3
  concurrency: 1

baserow:
  create_batch_size: 100
  update_batch_size: 100
  request_concurrency: 2
  max_retries: 3
  retry_backoff_seconds: 2

markets:
  target:
    - Spain
    - Portugal
    - France
    - Germany
    - Italy
    - Austria
    - Belgium
    - Netherlands
    - Ireland

supplier_gates:
  max_shipping_days: 10
  require_target_market: true
  require_shipping_policy: false
  require_return_policy: false
  minimum_data_completeness_pct: 60

supplier_scoring:
  reject_below: 60
  manual_review_from: 60
  approve_from: 75
  weights:
    market_coverage: 20
    shipping_speed: 20
    dispatch_proximity: 15
    data_completeness: 10
    shipping_policy: 10
    return_policy: 10
    rating: 5
    catalog_depth: 5
    approval_friction: 5

product_gates:
  max_shipping_days: 10
  minimum_margin_pct: 45
  require_known_supplier_price: true
  require_image: true
  require_in_stock: true

product_scoring:
  reject_below: 60
  manual_review_from: 60
  shortlist_from: 75
  weights:
    problem_solved: 20
    margin: 20
    shipping: 15
    content_potential: 15
    differentiation: 10
    return_risk: 10
    data_quality: 5
    supplier_strength: 5

margin:
  minimum_margin_pct: 45
  target_margin_pct: 55
  estimated_payment_fee_pct: 3
  estimated_platform_fee_pct: 2
  expected_return_allowance_pct: 5

incremental_scan:
  stop_after_known_products: 200
  stop_after_known_pages: 3

classification:
  minimum_confidence: 0.70

selection:
  initial_total_min: 18
  initial_total_max: 24
  target_per_collection_min: 6
  target_per_collection_max: 8
  max_supplier_share_pct: 30
  new_arrivals_batch_size: 4
```

---

## 33. CLI Requirements

Required commands:

```bash
syncee-scanner auth login
syncee-scanner auth validate

syncee-scanner discover

syncee-scanner scan full
syncee-scanner scan incremental
syncee-scanner scan reconcile
syncee-scanner scan resume <run_id>

syncee-scanner score suppliers
syncee-scanner score products
syncee-scanner classify products

syncee-scanner select initial
syncee-scanner select new-arrivals

syncee-scanner supplier approve <supplier_key>
syncee-scanner supplier block <supplier_key>
syncee-scanner supplier clear-override <supplier_key>

syncee-scanner product approve <product_key>
syncee-scanner product reject <product_key>

syncee-scanner runs list
syncee-scanner runs show <run_id>

syncee-scanner export suppliers
syncee-scanner export products
syncee-scanner export candidates
syncee-scanner export all
```

Common options:

```text
--config
--headless
--headed
--dry-run
--limit
--resume
--debug
--output-dir
```

---

## 34. Error Handling

Required error codes:

```text
AUTH_SESSION_EXPIRED
PAGE_LOAD_TIMEOUT
RATE_LIMITED
CAPTCHA_DETECTED
ACCESS_DENIED
PAGINATION_LOOP_DETECTED
PRODUCT_PARSE_FAILED
SUPPLIER_PARSE_FAILED
NETWORK_RESPONSE_CHANGED
BASEROW_API_ERROR
BASEROW_AUTH_ERROR
BASEROW_SCHEMA_MISMATCH
CONFIGURATION_ERROR
INCREMENTAL_ORDER_UNVERIFIED
CHECKPOINT_ERROR
```

### 34.1 Retry policy

Retry only transient errors:

- timeouts;
- temporary network failures;
- temporary Baserow errors;
- recoverable page-load failures;
- rate limiting with backoff.

Do not repeatedly retry:

- expired authentication;
- CAPTCHA;
- access denied;
- invalid configuration;
- missing required Baserow fields;
- unsupported page structure.

### 34.2 Debug artifacts

On browser or parsing failure, save:

```text
artifacts/errors/<run_id>/
  screenshot.png
  page.html
  url.txt
  error.json
  relevant_response.json
```

Do not save:

- access tokens;
- cookies;
- passwords;
- authorization headers.

---

## 35. Logging

Use structured logging.

Each log record should include where applicable:

- timestamp;
- level;
- run ID;
- command;
- page;
- cursor;
- product key;
- supplier key;
- operation;
- duration;
- result;
- error code.

Log files:

```text
logs/scanner.log
logs/scanner.jsonl
```

Console output should remain concise and show:

- run type;
- processed pages;
- processed products;
- new products;
- changed products;
- failed products;
- suppliers discovered;
- supplier scoring summary;
- product scoring summary;
- completeness status.

---

## 36. Performance and Politeness

Default:

```yaml
syncee:
  concurrency: 1
  page_delay_seconds: 2
  detail_page_delay_seconds: 2
  max_retries: 3
```

Requirements:

- add request jitter;
- slow down after rate-limit signals;
- avoid reopening supplier pages repeatedly;
- cache supplier details during a run;
- avoid downloading full-resolution images;
- store image URLs only;
- prefer structured API responses;
- use conservative concurrency.

---

## 37. Data Integrity Requirements

The system must guarantee:

1. unique Supplier Key;
2. unique Product Key;
3. valid product-to-supplier links;
4. idempotent repeated scans;
5. no silent deletion of historical data;
6. deterministic scoring for the same inputs and version;
7. traceable manual overrides;
8. score-version tracking;
9. configuration fingerprinting;
10. checkpoint persistence;
11. change history;
12. clear distinction between unknown, failed, and rejected states.

---

## 38. Optional CSV Export

CSV export is optional.

Commands:

```bash
syncee-scanner export suppliers
syncee-scanner export products
syncee-scanner export candidates
syncee-scanner export all
```

Export purposes:

- backup;
- offline review;
- migration;
- sharing.

CSV must use:

- UTF-8;
- stable column order;
- escaped multiline values;
- product and supplier keys;
- original URLs;
- export timestamp.

CSV must not become a dependency of normal operation.

---

## 39. Windmill Scheduling

Windmill scheduling is postponed until manual CLI runs are stable.

After validation, the intended weekly workflow is:

```text
Windmill Scheduled Job
        ↓
Run Incremental Scan
        ↓
Write New and Changed Records to Baserow
        ↓
Score New Suppliers
        ↓
Exclude Products From Failed Suppliers
        ↓
Score Remaining New Products
        ↓
Classify Products
        ↓
Create New Arrival Candidate Batch
        ↓
Manual Review in Baserow
```

Windmill must invoke the same CLI or application service used manually.

Do not create separate duplicated business logic in Windmill.

---

## 40. Suggested Project Structure

```text
syncee-scanner/
  pyproject.toml
  README.md
  .env.example
  config/
    default.yaml
    scoring.yaml
  src/
    syncee_scanner/
      cli.py
      config.py
      browser/
        auth.py
        session.py
        navigation.py
        network.py
      discovery/
        discover.py
        report.py
      extraction/
        products.py
        suppliers.py
        pagination.py
        normalization.py
      baserow/
        client.py
        schemas.py
        repositories.py
        indexes.py
        batching.py
      scoring/
        supplier_gates.py
        supplier_score.py
        product_gates.py
        product_score.py
        reason_codes.py
      classification/
        rules.py
        collections.py
        llm_fallback.py
      selection/
        initial.py
        new_arrivals.py
        diversity.py
      runs/
        manager.py
        checkpoints.py
        resume.py
      changes/
        fingerprints.py
        detector.py
      export/
        csv_export.py
        json_export.py
      observability/
        logging.py
        errors.py
        artifacts.py
  tests/
    unit/
    integration/
    fixtures/
  data/
    auth/
  artifacts/
  exports/
  logs/
```

---

## 41. Testing Requirements

### 41.1 Unit tests

Cover:

- URL normalization;
- Supplier Key generation;
- Product Key generation;
- country normalization;
- price normalization;
- date normalization;
- supplier deduplication;
- product deduplication;
- fingerprint generation;
- margin calculation;
- supplier hard gates;
- supplier scoring;
- product hard gates;
- product scoring;
- classification;
- initial selection;
- new-arrival selection;
- diversity constraints;
- checkpoint serialization;
- reason-code generation.

### 41.2 Integration tests

Use saved HTML and JSON fixtures.

Standard test suite must not require live Syncee access.

Cover:

- parsing product list response;
- parsing product details;
- parsing supplier details;
- multi-page pagination;
- cursor pagination;
- repeated page detection;
- interrupted scan;
- resume;
- Baserow batch create;
- Baserow batch update;
- duplicate prevention;
- new product detection;
- changed product detection;
- supplier rejection excluding products;
- new supplier scoring;
- initial assortment generation;
- new-arrival batch generation.

### 41.3 Smoke test

Live smoke test example:

```bash
syncee-scanner scan full --limit 50 --dry-run
```

Smoke test must:

- use existing session;
- scan limited products;
- avoid production status changes when dry-run is active;
- produce a run summary.

---

## 42. Implementation Phases

### Phase 0 — Discovery

Deliver:

- authentication flow;
- discovery command;
- route report;
- field report;
- pagination report;
- sort-order report;
- sample structured responses;
- decision on extraction strategy.

### Phase 1 — Baserow Setup

Deliver:

- database schema;
- table creation script or setup guide;
- field mapping;
- Baserow API client;
- batch create/update;
- lightweight indexes;
- schema validation.

### Phase 2 — Limited Scanner

Deliver:

- one subcategory;
- maximum 100 products;
- supplier extraction;
- product extraction;
- deduplication;
- Baserow persistence;
- run tracking;
- failure artifacts.

### Phase 3 — Full Category Scan

Deliver:

- all discovered pagination;
- checkpoints;
- resume;
- full Home & Kitchen scan;
- product and supplier changes;
- completeness reporting.

### Phase 4 — Supplier Scoring

Deliver:

- supplier hard gates;
- supplier weighted score;
- thresholds;
- reason codes;
- manual overrides;
- automatic product exclusion.

### Phase 5 — Product Scoring

Deliver:

- product hard gates;
- margin calculation;
- product weighted score;
- risk flags;
- collection classification.

### Phase 6 — Initial Assortment

Deliver:

- 18–24 product shortlist;
- collection balance;
- supplier diversity;
- price-point balance;
- Baserow selection batch;
- manual review flow.

### Phase 7 — Incremental Scan

Deliver:

- weekly scan;
- new product detection;
- new supplier flow;
- changed product detection;
- rescoring rules;
- completeness status.

### Phase 8 — New Arrivals

Deliver:

- 4-product candidate batch;
- collection mix;
- supplier diversity;
- Baserow batch creation;
- manual approval flow.

### Phase 9 — Reconciliation and Hardening

Deliver:

- inactive product detection;
- periodic refresh;
- stale record handling;
- improved tests;
- operational README;
- recovery procedures.

### Phase 10 — Optional Scheduling

Deliver only after manual validation:

- Windmill scheduled execution;
- notifications or run summaries;
- no duplicate business logic.

---

## 43. Acceptance Criteria

### 43.1 Authentication

- manual login works;
- session persists;
- headless reuse works;
- expired session is detected clearly.

### 43.2 Discovery

- stable product identity found;
- stable supplier identity found;
- pagination strategy documented;
- incremental feasibility documented;
- extraction fields documented.

### 43.3 Baserow

- all required tables exist;
- scanner validates schema;
- products link to suppliers;
- repeated scans do not create duplicates;
- batch operations are used;
- scan checkpoints are persisted.

### 43.4 Full scan

- all accessible Home & Kitchen pages or cursors are processed;
- interrupted run can resume;
- products and suppliers are stored;
- run completeness is recorded;
- failures are visible.

### 43.5 Supplier scoring

- every supplier receives gate status;
- eligible suppliers receive score;
- rejected suppliers receive reasons;
- manual override is auditable;
- rejected suppliers exclude their products.

### 43.6 Product scoring

- only supplier-eligible products are scored;
- every scored product has score version;
- hard-gate failures are explicit;
- incomplete margin is explicit;
- collection assignment is explicit;
- high-risk products do not pass automatically.

### 43.7 Initial assortment

- 18–24 candidates are generated when enough eligible products exist;
- target is 6–8 per collection;
- supplier concentration is controlled;
- every candidate includes reasons and risks;
- no automatic publication occurs.

### 43.8 Incremental scan

- known products are not marked as new;
- new Product Keys are detected;
- new suppliers are scored first;
- changed products are recorded;
- completeness uncertainty is not hidden.

### 43.9 New arrivals

- a 4-product batch is created when enough candidates exist;
- batch diversity rules are applied;
- products remain pending until manual approval.

### 43.10 Auditability

For any supplier or product, it must be possible to determine:

- first seen run;
- last seen run;
- current status;
- score;
- score version;
- failed gates;
- reason codes;
- manual overrides;
- selection batch;
- change history.

---

## 44. Definition of Done

Version 1 is complete when:

1. Syncee authentication works through saved browser state;
2. discovery confirms stable extraction paths;
3. Baserow schema is operational;
4. Baserow is the only persistent source of truth;
5. the accessible Home & Kitchen catalog can be scanned;
6. scans are resumable;
7. suppliers and products are deduplicated;
8. products are linked to suppliers;
9. supplier scoring runs before product scoring;
10. rejected suppliers automatically exclude their products;
11. eligible products receive product scores;
12. eligible products receive collection assignments;
13. initial selection produces 18–24 candidates;
14. weekly incremental scans identify new products;
15. new suppliers are scored before their products;
16. the system creates 4-product new-arrival candidate batches;
17. all important decisions include reason codes;
18. manual overrides are auditable;
19. product changes are stored;
20. Baserow views support review;
21. automated tests cover critical logic;
22. README explains setup, authentication, scans, scoring, review, resume, and recovery;
23. Shopify publication is not automatic;
24. CSV is optional only;
25. the solution stays within a simple, low-maintenance architecture.

---

## 45. Explicit Non-Goals

The coding agent must not expand this implementation into:

- a multi-agent system;
- a generic product-research platform;
- a custom dashboard;
- a distributed scraper;
- an autonomous Shopify operator;
- a vector-search platform;
- a machine-learning project;
- a proxy-rotation system;
- a large automation framework;
- a replacement for manual commercial judgment.

The first success criterion is:

> A reliable, resumable Syncee Home & Kitchen research pipeline that stores all operational data in Baserow and produces auditable supplier and product shortlists.
