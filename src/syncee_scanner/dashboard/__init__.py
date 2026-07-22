"""Review & approval dashboard (replaces Baserow's UI).

A FastAPI app over the Postgres store: browse/filter products, view image + SEO + score +
reason codes, and approve/reject/pin — writing back to the same JSONB rows the pipeline uses.
Processed images are served from the ``product_assets`` bytea table, so the stack is
self-contained (no external image host). Run: ``uvicorn syncee_scanner.dashboard.app:app``.
"""
