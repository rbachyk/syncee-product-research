"""Product source interface + adapters (spec §5.4, §8.4).

The scanner consumes an abstract :class:`ProductSource` that yields pages of *canonical raw*
product records (see :mod:`.records`). This is the seam the Discovery Gate protects: the
live :class:`SynceeSource` is written only after discovery confirms Syncee's routes,
pagination and response shapes, while :class:`FixtureSource` lets the whole pipeline and its
tests run offline against saved JSON (spec §41.2).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def _parse_offset_cursor(cursor: str | None) -> tuple[int, int]:
    """Parse an offset checkpoint cursor into ``(category_index, offset)``.

    Accepts ``"<cat>:<offset>"`` (multi-category) or a bare ``"<offset>"`` (single).
    """
    if not cursor:
        return 0, 0
    if ":" in cursor:
        cat, _, off = cursor.partition(":")
        return int(cat or 0), int(off or 0)
    return 0, int(cursor)


@dataclass
class SourcePage:
    """One page of raw product records from a source."""

    page_number: int
    products: list[dict[str, Any]]
    cursor: str | None = None
    has_next: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProductSource(Protocol):
    """Yields pages of canonical raw product dicts (newest-first when supported)."""

    def iter_pages(self, *, start_cursor: str | None = None) -> Iterator[SourcePage]: ...


class FixtureSource:
    """A source backed by saved JSON — a list of pages or a single flat product list.

    Fixture formats accepted:
      * ``{"pages": [{"products": [...], "cursor": "..."}, ...]}``
      * ``{"products": [...]}`` (single page)
      * ``[ {product}, {product}, ... ]`` (bare list, paginated by ``page_size``)
    """

    def __init__(self, data: dict | list, *, page_size: int = 100) -> None:
        self._pages = self._to_pages(data, page_size)

    @classmethod
    def from_file(cls, path: str | Path, *, page_size: int = 100) -> FixtureSource:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data, page_size=page_size)

    @staticmethod
    def _to_pages(data: dict | list, page_size: int) -> list[SourcePage]:
        if isinstance(data, dict) and "pages" in data:
            pages = []
            raw_pages = data["pages"]
            for i, p in enumerate(raw_pages):
                pages.append(
                    SourcePage(
                        page_number=i + 1,
                        products=p.get("products", []),
                        cursor=p.get("cursor"),
                        has_next=i < len(raw_pages) - 1,
                    )
                )
            return pages

        products = data["products"] if isinstance(data, dict) else data
        pages = []
        for i in range(0, len(products), page_size):
            chunk = products[i : i + page_size]
            pages.append(
                SourcePage(
                    page_number=i // page_size + 1,
                    products=chunk,
                    cursor=str(i + page_size),
                    has_next=i + page_size < len(products),
                )
            )
        return pages or [SourcePage(page_number=1, products=[], has_next=False)]

    def iter_pages(self, *, start_cursor: str | None = None) -> Iterator[SourcePage]:
        started = start_cursor is None
        for page in self._pages:
            if not started:
                # Resume: skip until we pass the checkpoint cursor.
                if page.cursor == start_cursor:
                    started = True
                continue
            yield page


class SynceeSource:
    """Live Syncee source: a declarative mapper + an injectable page transport.

    The *transport* is a callable ``fetch_page(cursor) -> dict`` returning the raw Syncee
    list response; the :class:`~.mapper.SynceeResponseMapper` turns each response into
    canonical raw products and reads the next cursor. This keeps the untestable part (the
    live Playwright network fetch) tiny and injectable, while pagination + mapping are pure
    and fully tested.

    The default transport (``None``) raises with a Discovery-Gate pointer, because the
    concrete list endpoint + response shape must be confirmed by ``discover`` first
    (spec §8.4) and encoded in ``config/syncee_mapping.yaml``.
    """

    def __init__(
        self,
        config=None,
        *,
        transport=None,
        mapper=None,
        max_pages: int = 100_000,
    ) -> None:
        from .mapper import SynceeResponseMapper

        self.config = config
        self._transport = transport
        self.mapper = mapper or SynceeResponseMapper()
        self.max_pages = max_pages

    def iter_pages(self, *, start_cursor: str | None = None) -> Iterator[SourcePage]:
        if self._transport is None:
            raise NotImplementedError(
                "SynceeSource needs a page transport. Run `syncee-scanner discover`, encode "
                "the confirmed response shape in config/syncee_mapping.yaml (spec §8.4), and "
                "inject a transport (see browser.transport.SynceeApiTransport)."
            )
        try:
            if self.mapper.mapping.list.mode == "offset":
                yield from self._iter_offset(start_cursor)
            else:
                yield from self._iter_cursor(start_cursor)
        finally:
            close = getattr(self._transport, "close", None)
            if callable(close):
                close()

    def _iter_cursor(self, start_cursor: str | None) -> Iterator[SourcePage]:
        cursor = start_cursor
        page_number = 0
        while page_number < self.max_pages:
            page_number += 1
            mapped = self.mapper.map_response(self._transport(cursor))
            yield SourcePage(
                page_number=page_number, products=mapped.products, cursor=mapped.next_cursor,
                has_next=mapped.has_next,
                meta={"raw_count": mapped.raw_count, "warnings": mapped.warnings},
            )
            if not mapped.has_next or not mapped.next_cursor:
                break
            cursor = mapped.next_cursor

    def _iter_offset(self, start_cursor: str | None) -> Iterator[SourcePage]:
        """Offset-paginated POST across one or more categories (spec §17).

        The transport is called with the full request body (a dict). When
        ``list.categories`` is set, each category is scanned in turn (its id overrides the
        template's ``category``). The checkpoint cursor is ``"<cat_index>:<offset>"`` so
        resume continues from the right category and offset.
        """
        list_cfg = self.mapper.mapping.list
        size = list_cfg.page_size
        template = dict(list_cfg.request_template or {})
        categories = list_cfg.categories or [template.get(list_cfg.category_param)]
        by_page = list_cfg.paginate_by == "page"
        first_pos = 1 if by_page else 0  # 1-based page numbers vs 0-based item offset

        per_cat_limit = list_cfg.per_category_limit or 0
        start_cat, start_offset = _parse_offset_cursor(start_cursor)
        page_number = 0
        for cat_index in range(start_cat, len(categories)):
            category = categories[cat_index]
            # Resume from the cursor's position for the start category; otherwise begin at
            # first_pos (1-based page, or offset 0).
            resuming = cat_index == start_cat and start_cursor is not None
            pos = start_offset if resuming else first_pos
            taken_in_cat = 0
            while page_number < self.max_pages:
                page_number += 1
                payload = {**template, list_cfg.offset_param: pos, list_cfg.size_param: size}
                if category is not None:
                    payload[list_cfg.category_param] = category
                mapped = self.mapper.map_response(self._transport(payload))
                next_pos = pos + (1 if by_page else size)
                taken_in_cat += len(mapped.products)
                consumed = (pos * size if by_page else next_pos)  # items fetched so far
                total = mapped.total
                cat_capped = per_cat_limit and taken_in_cat >= per_cat_limit
                cat_has_more = (
                    bool(mapped.products)
                    and (total is None or consumed < total)
                    and not cat_capped
                )
                more_categories = cat_index < len(categories) - 1
                yield SourcePage(
                    page_number=page_number, products=mapped.products,
                    cursor=f"{cat_index}:{next_pos}",
                    has_next=cat_has_more or more_categories,
                    meta={"category": category, "offset": pos, "total": total,
                          "raw_count": mapped.raw_count},
                )
                if not cat_has_more:
                    break
                pos = next_pos
