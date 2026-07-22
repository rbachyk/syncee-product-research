"""Optional CSV export (spec §38).

CSV is a convenience for backup / offline review / sharing / migration only — never a
dependency of normal operation (spec §5.2, §38). Exports use UTF-8, a stable column order,
escaped multiline values, the stable keys + original URLs, and an export timestamp column.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from ..baserow.schemas import PRODUCTS, SELECTION_BATCHES, SUPPLIERS, TableDef

# Selection statuses that count as "candidates" for the candidates export.
_CANDIDATE_STATUSES = {
    "Initial Assortment Candidate",
    "Initial Assortment Selected",
    "New Arrival Candidate",
    "New Arrival Selected",
}


def _columns(table: TableDef) -> list[str]:
    return [f.name for f in table.fields if f.name != "Raw Data"]


def _export_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat()


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    all_columns = [*columns, "Exported At"]
    stamp = _export_timestamp()
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=all_columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            record = {col: _cell(row.get(col)) for col in columns}
            record["Exported At"] = stamp
            writer.writerow(record)
    return path


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        # Link fields / multi-values -> join; keeps single-line CSV cells readable.
        return "; ".join(str(v) for v in value)
    return str(value)


def export_suppliers(rows: list[dict], out_dir: Path | str = "exports") -> Path:
    return _write_csv(Path(out_dir) / "suppliers.csv", _columns(SUPPLIERS), rows)


def export_products(rows: list[dict], out_dir: Path | str = "exports") -> Path:
    return _write_csv(Path(out_dir) / "products.csv", _columns(PRODUCTS), rows)


def export_candidates(rows: list[dict], out_dir: Path | str = "exports") -> Path:
    candidates = [r for r in rows if r.get("Selection Status") in _CANDIDATE_STATUSES]
    return _write_csv(Path(out_dir) / "candidates.csv", _columns(PRODUCTS), candidates)


def export_batches(rows: list[dict], out_dir: Path | str = "exports") -> Path:
    return _write_csv(Path(out_dir) / "selection_batches.csv", _columns(SELECTION_BATCHES), rows)
