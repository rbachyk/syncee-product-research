"""Export orchestration (spec §38).

Pulls rows from a persistence backend and writes CSV (and optionally JSON) files. Kept
optional and side-effect-free with respect to operational state.
"""

from __future__ import annotations

from pathlib import Path

from . import csv_export
from .json_export import export_json


def export_suppliers(persistence, out_dir: Path | str = "exports") -> list[Path]:
    rows = persistence.iter_suppliers()
    return [csv_export.export_suppliers(rows, out_dir)]


def export_products(persistence, out_dir: Path | str = "exports") -> list[Path]:
    rows = persistence.iter_products()
    return [csv_export.export_products(rows, out_dir)]


def export_candidates(persistence, out_dir: Path | str = "exports") -> list[Path]:
    rows = persistence.iter_products()
    return [csv_export.export_candidates(rows, out_dir)]


def export_all(persistence, out_dir: Path | str = "exports", *, json: bool = False) -> list[Path]:
    suppliers = persistence.iter_suppliers()
    products = persistence.iter_products()
    written = [
        csv_export.export_suppliers(suppliers, out_dir),
        csv_export.export_products(products, out_dir),
        csv_export.export_candidates(products, out_dir),
    ]
    if json:
        written.append(export_json(suppliers, Path(out_dir) / "suppliers.json"))
        written.append(export_json(products, Path(out_dir) / "products.json"))
    return written
