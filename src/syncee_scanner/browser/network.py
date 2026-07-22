"""Network response capture (spec §5.4, §8.2).

Attaches to a Playwright page and records JSON XHR/GraphQL responses so the scanner can
prefer structured API data over fragile DOM scraping (spec §5.4). The buffer is also what
discovery samples to produce ``network_endpoints.json`` and sample responses (spec §8.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapturedResponse:
    url: str
    method: str
    status: int
    resource_type: str
    body: Any | None = None  # parsed JSON when available
    request_body: str | None = None  # POST/GraphQL request payload, if any


@dataclass
class ResponseRecorder:
    """Buffers captured JSON responses, optionally filtered by URL substring."""

    url_filters: tuple[str, ...] = ()
    responses: list[CapturedResponse] = field(default_factory=list)

    def should_record(self, url: str) -> bool:
        if not self.url_filters:
            return True
        return any(f in url for f in self.url_filters)

    def record(self, response: CapturedResponse) -> None:
        if self.should_record(response.url):
            self.responses.append(response)

    def matching(self, needle: str) -> list[CapturedResponse]:
        return [r for r in self.responses if needle in r.url]

    def latest(self, needle: str) -> CapturedResponse | None:
        found = self.matching(needle)
        return found[-1] if found else None

    def attach(self, page: object) -> None:
        """Wire this recorder to a Playwright page's ``response`` event."""

        def _on_response(response):  # pragma: no cover - requires live browser
            try:
                url = response.url
                if not self.should_record(url):
                    return
                body = None
                ctype = response.headers.get("content-type", "")
                if "application/json" in ctype:
                    try:
                        body = response.json()
                    except Exception:
                        body = None
                request_body = None
                try:
                    request_body = response.request.post_data
                except Exception:
                    request_body = None
                self.record(
                    CapturedResponse(
                        url=url,
                        method=response.request.method,
                        status=response.status,
                        resource_type=response.request.resource_type,
                        body=body,
                        request_body=request_body,
                    )
                )
            except Exception:
                return

        page.on("response", _on_response)  # type: ignore[attr-defined]
