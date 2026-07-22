"""Unit tests for the discovery endpoint summarizer (spec §8.2)."""

from syncee_scanner.browser.network import CapturedResponse, ResponseRecorder
from syncee_scanner.discovery.discover import _summarize_endpoints


def test_dedupes_endpoints_by_method_and_path():
    rec = ResponseRecorder()
    rec.record(CapturedResponse("https://x/api/products?page=1", "GET", 200, "xhr", {"a": 1}))
    rec.record(CapturedResponse("https://x/api/products?page=2", "GET", 200, "xhr", {"a": 2}))
    rec.record(CapturedResponse("https://x/api/suppliers", "GET", 200, "xhr", None))
    summary = _summarize_endpoints(rec)
    endpoints = {s["endpoint"] for s in summary}
    assert endpoints == {"https://x/api/products", "https://x/api/suppliers"}
    products = next(s for s in summary if s["endpoint"].endswith("products"))
    assert products["has_json_body"] is True
    suppliers = next(s for s in summary if s["endpoint"].endswith("suppliers"))
    assert suppliers["has_json_body"] is False
