"""Pagination termination & loop detection (spec §17.4)."""

from __future__ import annotations

from ..observability.errors import PaginationLoopError


class PaginationGuard:
    """Detects repeated pages/cursors and enforces a safety page cap (spec §17.4).

    Call :meth:`check` once per page *before* processing it. Raises
    PAGINATION_LOOP_DETECTED when the same cursor repeats or the safety limit is hit.
    """

    def __init__(self, *, max_pages: int) -> None:
        self.max_pages = max_pages
        self.pages_seen = 0
        self._seen_cursors: set[str] = set()
        self._last_cursor: str | None = None

    def check(self, *, page_number: int, cursor: str | None) -> None:
        self.pages_seen += 1
        if self.pages_seen > self.max_pages:
            raise PaginationLoopError(
                f"Safety page limit {self.max_pages} reached",
                context={"page": page_number},
            )
        if cursor is not None:
            if cursor == self._last_cursor or cursor in self._seen_cursors:
                raise PaginationLoopError(
                    "Cursor repeated unexpectedly",
                    context={"page": page_number, "cursor": cursor},
                )
            self._seen_cursors.add(cursor)
            self._last_cursor = cursor
