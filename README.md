# Syncee Product Research Pipeline (RB Home)

A deterministic, resumable research pipeline that scans the authenticated Syncee
**Home & Kitchen** marketplace, scores suppliers and products, classifies survivors into
RB Home collections, and produces auditable shortlists. **Baserow is the single source of
truth** — no SQLite, and nothing is published automatically.

See [`SYNCEE_PRODUCT_RESEARCH_PIPELINE_SPEC_V1.md`](SYNCEE_PRODUCT_RESEARCH_PIPELINE_SPEC_V1.md)
for the full functional/technical specification.

## Status

Implemented and tested (offline, no live Syncee needed): **Phases 0–9** — auth/discovery
scaffolding, the scanner with dedup + Baserow persistence + resume, supplier scoring + gates
+ manual overrides (§20–21), product scoring + margin + risk + classification (§22–25),
initial-assortment / new-arrivals selection (§26, §29), the weekly incremental scan with
Product Changes tracking (§27, §13), reconciliation (§28), CSV/JSON export (§38), and Baserow
operational views (§30). Every CLI command is implemented. The full scan → score → select and
scan → incremental → score pipelines run end to end in the test suite (150+ tests).

The live extraction path is **declarative**: `discover` records where each field lives in
Syncee's API responses, you fill those dotted paths into
[`config/syncee_mapping.yaml`](config/syncee_mapping.yaml), and the tested mapper +
`SynceeSource` do the rest — no code changes. The only remaining work is confirming that
mapping against a real account (spec §8.4) and optional Windmill scheduling (Phase 10).

## Setup

```bash
# 1. Create the environment and install (Python 3.12+)
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"

# 2. Install the Chromium browser used by Playwright
.venv/bin/python -m playwright install chromium

# 3. Configure secrets
cp .env.example .env      # fill in BASEROW_DATABASE_TOKEN (+ table IDs)

# 4. Create the Baserow schema. Pick the one that matches your situation:
#    a) You already created the database + empty tables and put their IDs in .env
#       -> add the fields to those tables (prompts for password; idempotent):
.venv/bin/syncee-scanner baserow setup-fields
#    b) Nothing created yet, want it fully automatic (creates DB + tables + fields + views):
.venv/bin/syncee-scanner baserow setup
#    c) Prefer no password at all -> create everything by hand from a generated guide:
.venv/bin/syncee-scanner baserow guide
```

**The scanner reaches the Baserow API with only `BASEROW_DATABASE_TOKEN`.** A Baserow token
can CRUD rows but **cannot create tables, fields, or views** — those are schema operations
that require a user JWT. So `baserow setup` / `setup-fields` / `views` authenticate with your
email + password (prompted, never stored) purely to build the schema. `setup-fields` targets
the table IDs already in your `.env`: it renames the primary field, creates only the missing
fields, and deletes nothing.

## Core commands

```bash
syncee-scanner auth login          # manual Syncee login (headed), saves browser session
syncee-scanner auth validate       # verify the saved session still works

syncee-scanner discover            # inspect Syncee structure -> artifacts/discovery/

syncee-scanner scan full --limit 50 --dry-run   # limited smoke scan, no status changes
syncee-scanner scan full --fixture tests/fixtures/home_kitchen_products.json --dry-run  # offline

syncee-scanner score suppliers     # supplier gates + weighted scoring (§20)
syncee-scanner score products      # product gates + margin + scoring + classification (§22-25)
syncee-scanner select initial      # 18-24 product initial-assortment candidate batch (§26)
syncee-scanner select new-arrivals # 4-product new-arrivals candidate batch (§29)

syncee-scanner supplier approve|block|clear-override <supplier_key>   # audited overrides (§20.8)
syncee-scanner product approve|reject <product_key>                   # audited overrides (§14)

syncee-scanner runs list           # recent scan runs
syncee-scanner runs show <run_id>  # one run's detail
```

Scoring, selection and override commands operate on already-persisted rows, so they require
Baserow to be configured. `scan full --fixture … --dry-run` runs fully offline.

Common options: `--config --headless --headed --dry-run --limit --resume --debug
--output-dir` (spec §33).

## Configuration

All operational tunables live in [`config/default.yaml`](config/default.yaml) and scoring
weights/thresholds in [`config/scoring.yaml`](config/scoring.yaml) (spec §32). Scoring logic
lives only in code + config, never in Baserow formulas. Override with a local file via
`--config config/local.yaml`.

## Development

```bash
.venv/bin/pytest            # unit + integration tests (no live Syncee access needed)
.venv/bin/ruff check src tests
```

Tests use saved HTML/JSON fixtures; the standard suite never touches live Syncee (spec §41).

## Going live (Discovery Gate, spec §8.4)

1. `syncee-scanner auth login` then `auth validate`.
2. `syncee-scanner discover` — inspect `artifacts/discovery/sample_product_list_response.json`.
3. Edit [`config/syncee_mapping.yaml`](config/syncee_mapping.yaml): set `list.endpoint_template`
   (with a `{cursor}` placeholder), the `products_path` / cursor paths, and each product/
   supplier field's dotted path. No code changes are required.
4. `syncee-scanner baserow setup` (creates DB + tables + views), paste the printed IDs into `.env`.
5. `syncee-scanner scan full --limit 50` to smoke-test, then the full scan.

## Weekly operating workflow (spec §39)

Run every 1–2 weeks (≈15 min):

```bash
syncee-scanner scan incremental --newest-first-verified   # scan + score new/changed (§27, §39)
syncee-scanner select new-arrivals                         # build the 4-product candidate batch (§29)
```

Then review the **New Arrival Candidates** view in Baserow and approve products manually. The
initial catalogue is a one-time `scan full` → `score suppliers` → `score products` →
`select initial`, then manual review of the **Initial Assortment Candidates** view.

## Recovery procedures

- **Expired session** (`AUTH_SESSION_EXPIRED`): re-run `auth login`.
- **Interrupted scan**: `syncee-scanner runs list` to find the run ID, then
  `scan resume <run_id>` — it continues from the last checkpoint; upserts are idempotent so
  re-processing a page never duplicates rows (§16.5).
- **Baserow schema drift** (`BASEROW_SCHEMA_MISMATCH`): a required field is missing/renamed;
  the scanner uses field IDs internally, so re-add the field or re-run `baserow setup` on a
  fresh database.
- **Rate limiting** (`RATE_LIMITED`): the scanner backs off automatically; lower
  `syncee.concurrency` / raise `page_delay_seconds` if it persists.
- **Failure artifacts**: on browser/parse errors see `artifacts/errors/<run_id>/`
  (screenshot, page HTML, redacted error JSON) — secrets are never written (§34.2).
- **Missing / discontinued products**: `scan reconcile` marks unseen products inactive
  (never deletes) and reactivates them if they reappear (§28).
