# CLAUDE.md

Guidance for working in this repository.

## What this is

A deterministic, resumable **Syncee Home & Kitchen product-research pipeline** for RB Home.
It scans the authenticated Syncee marketplace, scores suppliers then products, classifies
them into RB Home collections, and produces auditable shortlists. **Baserow is the only
persistent store** (no SQLite). Nothing publishes automatically. Full spec:
`SYNCEE_PRODUCT_RESEARCH_PIPELINE_SPEC_V1.md` (sections are cited as `§N` throughout the code).

## Commands

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -e ".[dev]"
.venv/bin/pytest                         # full suite, no live access needed
.venv/bin/pytest --cov=syncee_scanner    # with coverage
.venv/bin/ruff check src tests           # lint
.venv/bin/syncee-scanner --help          # CLI
# Offline end-to-end smoke:
.venv/bin/syncee-scanner scan full --fixture tests/fixtures/home_kitchen_products.json --dry-run
```

## Architecture (one-way dependency flow)

```
cli.py (Typer)
  scan.py / incremental.py / reconcile.py  — orchestrators
    extraction/  source (Fixture|Syncee) → records (normalize) → keys, pagination, mapper
    baserow/     client, schemas, mapping, indexes, batching, repositories, persistence, views, setup
    scoring/     supplier_gates+score, product_gates+score, margin, reason_codes, service, overrides
    classification/  rules + collections (deterministic), llm_fallback (stub)
    selection/   diversity, initial, new_arrivals, service
    changes/     fingerprints, detector
    runs/        manager, checkpoints, persistence (protocols + InMemory backend)
    observability/  logging, errors, artifacts
```

## Load-bearing conventions (follow these)

- **Deterministic logic lives in code + YAML only, never Baserow formulas** (§5.5). All
  tunables come from `config/default.yaml` + `config/scoring.yaml`; no magic numbers.
- **Persistence is behind protocols** in `runs/persistence.py`: `ScanPersistence` +
  `ReviewOps`. `InMemoryPersistence` (same file) mirrors the Baserow backend and is what the
  tests drive. `baserow/persistence.py::BaserowPersistence` is the live implementation.
  When you add a persistence operation, add it to the protocol **and both backends**.
- **The extraction seam is the Discovery Gate** (§8.4). `FixtureSource` runs everything
  offline. `SynceeSource` = injectable `fetch_page(cursor)` transport + declarative
  `extraction/mapper.py` driven by `config/syncee_mapping.yaml` (dotted paths). Going live =
  fill in that YAML, not writing code. `browser/transport.py` is the only live-only fetch.
- **Idempotent upserts** (`baserow/repositories.py`): classify new/changed/unchanged by
  fingerprint (`changes/fingerprints.py`); `First Seen At` via `create_extra`, never
  overwritten. Re-scanning never duplicates rows (§16.5).
- **Every decision is auditable**: reason codes (`scoring/reason_codes.py`), score versions,
  Product Changes rows (§13), and an immutable Manual Decisions trail for overrides (§14).
- **Errors** use the `ErrorCode` enum + `ScannerError` (`observability/errors.py`); retry
  policy is `ScannerError.retryable`. Debug artifacts redact secrets (`artifacts.py`, §34.2).
- **CLI**: commands acting on persisted rows (score/select/override/export/incremental/
  reconcile/resume/views) require Baserow and exit 2 if unconfigured; `_build_persistence`
  falls back to in-memory only for offline `scan full --fixture`.
- **LLM access** (the optional classification fallback, `classification/llm_fallback.py`) is
  ALWAYS via OpenRouter (`OPENROUTER_API_KEY`) or a subscription CLI — **never** a direct
  provider API. Do not add direct-provider API keys anywhere; use OpenRouter-style
  `provider/model` ids. See `classification.llm` in `config/default.yaml`.

## Testing

`tests/unit` = pure logic; `tests/integration` = orchestration with `InMemoryPersistence` or
respx-mocked Baserow HTTP. The suite **never** touches live Syncee (§41). When adding a
feature, prefer a pure function + a targeted unit test; wire it into an orchestrator/CLI last.

## Not built (intentionally)

- Live `SynceeSource` field paths — confirmed by `discover` against a real account (§8.4).
- Windmill scheduling (Phase 10) — deferred by spec until manual runs are validated (§39).
Avoid the §45 non-goals (multi-agent system, dashboard, vector DB, proxy rotation, auto-publish).
