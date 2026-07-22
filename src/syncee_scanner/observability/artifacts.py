"""Debug artifact capture on failure (spec §34.2).

Writes ``artifacts/errors/<run_id>/`` with a screenshot, page HTML, URL, error JSON and
the relevant network response — with secret redaction. Never persists access tokens,
cookies, passwords or authorization headers (spec §34.2, §7.1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .logging import get_logger

log = get_logger(__name__)

# Header / key names that must never be written to disk (spec §34.2).
_REDACT_KEYS = re.compile(
    r"(authorization|cookie|set-cookie|token|password|secret|api[-_]?key|x-auth)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


def redact(value: Any) -> Any:
    """Recursively redact secret-looking keys from dicts/lists before persisting."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _REDACT_KEYS.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class ArtifactWriter:
    """Persist debug artifacts for a run under ``artifacts/errors/<run_id>/``."""

    def __init__(self, run_id: str, base_dir: Path | str = Path("artifacts")) -> None:
        self.run_id = run_id
        self.dir = Path(base_dir) / "errors" / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def write_error(
        self,
        *,
        error: dict,
        url: str | None = None,
        page_html: str | None = None,
        screenshot_bytes: bytes | None = None,
        relevant_response: Any | None = None,
    ) -> Path:
        """Write the standard failure bundle. Returns the artifact directory."""
        self._write_json("error.json", redact(error))
        if url is not None:
            (self.dir / "url.txt").write_text(url, encoding="utf-8")
        if page_html is not None:
            (self.dir / "page.html").write_text(page_html, encoding="utf-8")
        if screenshot_bytes is not None:
            (self.dir / "screenshot.png").write_bytes(screenshot_bytes)
        if relevant_response is not None:
            self._write_json("relevant_response.json", redact(relevant_response))
        log.warning("artifact.error_written", run_id=self.run_id, path=str(self.dir))
        return self.dir

    def _write_json(self, name: str, data: Any) -> None:
        (self.dir / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
