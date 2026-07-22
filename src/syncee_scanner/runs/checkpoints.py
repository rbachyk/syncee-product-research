"""Scan checkpoints (spec §17.5, §17.6).

A checkpoint captures enough state to resume a scan from the next safe page/cursor. It is
serialized to the Scan Runs ``Checkpoint Data`` field after each page/batch (spec §17.3
step 13) and restored on resume.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..extraction.normalization import now_iso
from ..observability.errors import CheckpointError


@dataclass
class Checkpoint:
    """Resumable scan position (spec §17.5 JSON shape)."""

    page: int = 0
    cursor: str | None = None
    last_product_key: str | None = None
    products_processed: int = 0
    suppliers_processed: int = 0
    updated_at: str | None = None

    def to_json(self) -> str:
        data = asdict(self)
        data["updated_at"] = self.updated_at or now_iso()
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str | None) -> Checkpoint:
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CheckpointError(f"Corrupt checkpoint data: {exc}") from exc
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})
