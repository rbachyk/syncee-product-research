"""Optional JSON export (spec §38).

Mirrors :mod:`.csv_export` for callers who prefer structured JSON (backup / migration).
Includes the stable keys + original URLs and an export timestamp. Never a dependency of
normal operation (spec §5.2, §38).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def export_json(rows: list[dict], path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "count": len(rows),
        "rows": rows,
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return path
