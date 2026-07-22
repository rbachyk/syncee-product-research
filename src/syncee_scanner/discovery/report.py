"""Discovery artifact writer (spec §8.3).

Given whatever discovery captured, write the standard ``artifacts/discovery/`` output set.
Pure file I/O so it can be unit-tested with synthetic capture data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DiscoveryFindings:
    """Everything discovery learned, ready to serialize (spec §8.2/§8.3)."""

    routes: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    pagination: dict[str, Any] = field(default_factory=dict)
    sort_options: dict[str, Any] = field(default_factory=dict)
    network_endpoints: list[dict[str, Any]] = field(default_factory=list)
    sample_product_list_response: Any = None
    sample_product_detail_response: Any = None
    sample_supplier_response: Any = None
    notes: list[str] = field(default_factory=list)

    def gate_ready(self) -> bool:
        """Whether the Discovery Gate (spec §8.4) preconditions look satisfied."""
        return bool(
            self.routes.get("product_identity")
            and self.routes.get("supplier_identity")
            and self.pagination.get("strategy")
            and self.pagination.get("extraction_method")
        )


def write_discovery_artifacts(
    findings: DiscoveryFindings, *, output_dir: str | Path = "artifacts/discovery"
) -> Path:
    """Write all discovery artifacts and return the output directory (spec §8.3)."""
    out = Path(output_dir)
    (out / "screenshots").mkdir(parents=True, exist_ok=True)

    _dump(out / "routes.json", findings.routes)
    _dump(out / "fields.json", findings.fields)
    _dump(out / "pagination.json", findings.pagination)
    _dump(out / "sort_options.json", findings.sort_options)
    _dump(out / "network_endpoints.json", findings.network_endpoints)
    _dump(out / "sample_product_list_response.json", findings.sample_product_list_response)
    _dump(out / "sample_product_detail_response.json", findings.sample_product_detail_response)
    _dump(out / "sample_supplier_response.json", findings.sample_supplier_response)
    (out / "discovery_report.md").write_text(_render_report(findings), encoding="utf-8")
    return out


def _dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _render_report(f: DiscoveryFindings) -> str:
    note_lines = [f"- {n}" for n in f.notes] or ["- (none)"]
    lines = [
        "# Syncee Discovery Report",
        "",
        f"**Discovery Gate ready:** {'YES' if f.gate_ready() else 'NO — see notes'}",
        "",
        "## Routes",
        "```json",
        json.dumps(f.routes, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Pagination strategy",
        "```json",
        json.dumps(f.pagination, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Sort options",
        "```json",
        json.dumps(f.sort_options, indent=2, ensure_ascii=False),
        "```",
        "",
        f"## Network endpoints observed ({len(f.network_endpoints)})",
        "```json",
        json.dumps(f.network_endpoints[:50], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Notes",
        *note_lines,
        "",
        "## Gate checklist (spec §8.4)",
        "- [ ] Stable product identity",
        "- [ ] Stable supplier identity",
        "- [ ] Viable pagination / cursor strategy",
        "- [ ] Viable extraction method",
        "- [ ] Incremental newest-first feasibility documented",
    ]
    return "\n".join(lines) + "\n"
