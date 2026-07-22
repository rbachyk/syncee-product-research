"""PostgresPersistence — JSONB-backed store implementing the persistence protocols.

Mirrors :class:`~..runs.persistence.InMemoryPersistence` (the reference implementation) exactly,
including idempotent upsert-by-fingerprint semantics (§16.5), but backed by Postgres so it holds
the full catalogue without the row-count limits/timeouts of the Baserow REST API.

Schema: each entity is ``id BIGSERIAL PK, <key> TEXT UNIQUE, data JSONB``. The row dict returned
to the pipeline is ``{**data, "id": id}`` — identical shape to the other backends. Indexed
expressions on the JSONB (collection / review status / selection status) keep the dashboard fast.
"""

from __future__ import annotations

import json

import psycopg
from psycopg.types.json import Jsonb

from ..baserow.indexes import KeyIndex
from ..baserow.repositories import UpsertRecord, UpsertResult
from ..models import CompletenessStatus, RunStatus, RunType
from ..runs.checkpoints import Checkpoint
from ..runs.manager import RunCounts, RunHandle, new_run_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id BIGSERIAL PRIMARY KEY,
    supplier_key TEXT UNIQUE NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    product_key TEXT UNIQUE NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_products_collection ON products ((data->>'Collection'));
CREATE INDEX IF NOT EXISTS idx_products_review ON products ((data->>'Review Status'));
CREATE INDEX IF NOT EXISTS idx_products_selection ON products ((data->>'Selection Status'));
CREATE INDEX IF NOT EXISTS idx_products_supplier ON products ((data->'Supplier'));
CREATE TABLE IF NOT EXISTS scan_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT UNIQUE NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    checkpoint JSONB
);
CREATE TABLE IF NOT EXISTS selection_batches (
    id BIGSERIAL PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS manual_decisions (
    id BIGSERIAL PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS product_changes (
    id BIGSERIAL PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS product_assets (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'image/jpeg',
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assets_product ON product_assets (product_id);
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,                       -- 'scan' | 'enrich'
    status TEXT NOT NULL DEFAULT 'running',   -- 'running' | 'succeeded' | 'failed'
    params JSONB NOT NULL DEFAULT '{}',
    log TEXT NOT NULL DEFAULT '',
    pid INTEGER,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
"""


class PostgresPersistence:
    """Full persistence backend over Postgres (ScanPersistence + ReviewOps)."""

    def __init__(self, dsn: str, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._conn = psycopg.connect(dsn, autocommit=True)
        self.init_schema()

    def init_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # --- helpers -------------------------------------------------------------------

    def _rows(self, table: str) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT id, data FROM {table}")  # noqa: S608 - table is a constant
            return [{**data, "id": rid} for rid, data in cur.fetchall()]

    def _index(self, table: str, key_col: str) -> KeyIndex:
        index = KeyIndex()
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT id, {key_col}, data->>'Record Fingerprint' FROM {table}")  # noqa: S608
            for rid, key, fp in cur.fetchall():
                index.add(key, rid, fp)
        return index

    def _upsert(self, records: list[UpsertRecord], index: KeyIndex, table: str,
                key_col: str) -> UpsertResult:
        result = UpsertResult()
        with self._conn.cursor() as cur:
            for rec in records:
                entry = index.get(rec.key)
                if entry is None:
                    fields = {**rec.fields, **rec.create_extra,
                              key_col_name(key_col): rec.key,
                              "Record Fingerprint": rec.fingerprint}
                    cur.execute(
                        f"INSERT INTO {table} ({key_col}, data) VALUES (%s, %s) RETURNING id",  # noqa: S608
                        (rec.key, Jsonb(fields)),
                    )
                    row_id = cur.fetchone()[0]
                    index.add(rec.key, row_id, rec.fingerprint)
                    result.created += 1
                    result.key_to_row_id[rec.key] = row_id
                elif entry.fingerprint != rec.fingerprint:
                    patch = {**rec.fields, **rec.changed_extra,
                             "Record Fingerprint": rec.fingerprint}
                    cur.execute(
                        f"UPDATE {table} SET data = data || %s WHERE id = %s",  # noqa: S608
                        (Jsonb(patch), entry.row_id),
                    )
                    index.add(rec.key, entry.row_id, rec.fingerprint)
                    result.updated += 1
                    result.changed_keys.append(rec.key)
                    result.key_to_row_id[rec.key] = entry.row_id
                else:
                    if rec.touch_fields:
                        cur.execute(
                            f"UPDATE {table} SET data = data || %s WHERE id = %s",  # noqa: S608
                            (Jsonb(rec.touch_fields), entry.row_id),
                        )
                    result.unchanged += 1
                    result.key_to_row_id[rec.key] = entry.row_id
        return result

    def _update_by_id(self, table: str, row_id: int, fields: dict) -> None:
        if self.dry_run:
            return
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET data = data || %s WHERE id = %s",  # noqa: S608
                (Jsonb(fields), row_id),
            )

    def _create(self, table: str, fields: dict) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} (data) VALUES (%s) RETURNING id",  # noqa: S608
                (Jsonb(fields),),
            )
            return cur.fetchone()[0]

    # --- ScanPersistence -----------------------------------------------------------

    def load_supplier_index(self) -> KeyIndex:
        return self._index("suppliers", "supplier_key")

    def load_product_index(self) -> KeyIndex:
        return self._index("products", "product_key")

    def create_run(self, *, run_type: RunType, category: str, config_hash: str,
                   scanner_version: str) -> RunHandle:
        run_id = new_run_id(run_type)
        fields = {
            "Run Type": run_type.value, "Status": RunStatus.RUNNING.value, "Category": category,
            "Configuration Hash": config_hash, "Scanner Version": scanner_version,
        }
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_runs (run_id, data) VALUES (%s, %s) RETURNING id",
                (run_id, Jsonb(fields)),
            )
            row_id = cur.fetchone()[0]
        return RunHandle(run_id=run_id, row_id=row_id)

    def upsert_suppliers(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult:
        return self._upsert(records, index, "suppliers", "supplier_key")

    def upsert_products(self, records: list[UpsertRecord], index: KeyIndex) -> UpsertResult:
        return self._upsert(records, index, "products", "product_key")

    def save_checkpoint(self, run: RunHandle, checkpoint: Checkpoint, counts: RunCounts) -> None:
        with self._conn.cursor() as cur:
            cur.execute("UPDATE scan_runs SET checkpoint = %s WHERE run_id = %s",
                        (Jsonb(json.loads(checkpoint.to_json())), run.run_id))

    def load_run(self, run_id: str) -> tuple[RunHandle, Checkpoint] | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id, checkpoint FROM scan_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        if not row:
            return None
        row_id, cp = row
        checkpoint = Checkpoint.from_json(json.dumps(cp) if cp else None)
        return RunHandle(run_id=run_id, row_id=row_id), checkpoint

    def complete_run(self, run: RunHandle, *, status: RunStatus,
                     completeness: CompletenessStatus, counts: RunCounts,
                     error_summary: str | None = None) -> None:
        patch = {"Status": status.value, "Completeness Status": completeness.value,
                 "Error Summary": error_summary, **counts.as_baserow_fields()}
        with self._conn.cursor() as cur:
            cur.execute("UPDATE scan_runs SET data = data || %s WHERE run_id = %s",
                        (Jsonb(patch), run.run_id))

    # --- ReviewOps -----------------------------------------------------------------

    def iter_suppliers(self) -> list[dict]:
        return self._rows("suppliers")

    def iter_products(self) -> list[dict]:
        return self._rows("products")

    def update_supplier(self, row_id: int, fields: dict) -> None:
        self._update_by_id("suppliers", row_id, fields)

    def update_product(self, row_id: int, fields: dict) -> None:
        self._update_by_id("products", row_id, fields)

    def update_supplier_rows(self, items: list[dict]) -> None:
        for item in items:
            self._update_by_id(
                "suppliers", item["id"], {k: v for k, v in item.items() if k != "id"})

    def update_product_rows(self, items: list[dict]) -> None:
        for item in items:
            self._update_by_id(
                "products", item["id"], {k: v for k, v in item.items() if k != "id"})

    def set_product_image(self, row_id: int, content: bytes, filename: str) -> str | None:
        """Store the processed image bytes in Postgres; the dashboard serves them at /asset/{id}."""
        if self.dry_run:
            return None
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO product_assets (product_id, filename, content) "
                "VALUES (%s, %s, %s) RETURNING id",
                (row_id, filename, content),
            )
            asset_id = cur.fetchone()[0]
        url = f"/asset/{asset_id}"
        # Mirror the Baserow file-field shape so publishing/service reads it identically.
        self._update_by_id(
            "products", row_id, {"Processed Image": [{"url": url, "name": filename}]}
        )
        return url

    def create_selection_batch(self, fields: dict, product_row_ids: list[int]) -> int:
        if self.dry_run:
            return 0
        return self._create("selection_batches", {**fields, "Products": product_row_ids})

    def create_manual_decision(self, fields: dict) -> int:
        if self.dry_run:
            return 0
        return self._create("manual_decisions", fields)

    def create_product_change(self, fields: dict) -> int:
        if self.dry_run:
            return 0
        return self._create("product_changes", fields)


def key_col_name(key_col: str) -> str:
    """Map the DB key column to the field name stored in JSONB ('Product Key'/'Supplier Key')."""
    return {"product_key": "Product Key", "supplier_key": "Supplier Key"}[key_col]
