"""Unit tests for error handling + debug-artifact redaction (spec §34)."""

import json

from syncee_scanner.observability.artifacts import ArtifactWriter, redact
from syncee_scanner.observability.errors import ErrorCode, ScannerError


class TestRedact:
    def test_redacts_secret_keys_recursively(self):
        data = {
            "Authorization": "Bearer abc",
            "Cookie": "session=1",
            "name": "keep",
            "nested": {"api_key": "x", "ok": 1},
            "list": [{"password": "p"}, {"fine": 2}],
        }
        out = redact(data)
        assert out["Authorization"] == "***REDACTED***"
        assert out["Cookie"] == "***REDACTED***"
        assert out["name"] == "keep"
        assert out["nested"]["api_key"] == "***REDACTED***"
        assert out["nested"]["ok"] == 1
        assert out["list"][0]["password"] == "***REDACTED***"
        assert out["list"][1]["fine"] == 2

    def test_non_dict_passthrough(self):
        assert redact("plain") == "plain"
        assert redact(42) == 42


class TestArtifactWriter:
    def test_writes_bundle_with_redaction(self, tmp_path):
        writer = ArtifactWriter("run-1", base_dir=tmp_path)
        path = writer.write_error(
            error={"error_code": "X", "authorization": "secret-token"},
            url="https://app.syncee.com/x",
            page_html="<html>hi</html>",
            screenshot_bytes=b"\x89PNG",
            relevant_response={"token": "leak", "data": [1, 2]},
        )
        assert (path / "url.txt").read_text() == "https://app.syncee.com/x"
        assert (path / "page.html").exists()
        assert (path / "screenshot.png").read_bytes() == b"\x89PNG"
        # secrets redacted in both error.json and relevant_response.json
        err = json.loads((path / "error.json").read_text())
        assert err["authorization"] == "***REDACTED***"
        resp = json.loads((path / "relevant_response.json").read_text())
        assert resp["token"] == "***REDACTED***"
        assert resp["data"] == [1, 2]


class TestScannerError:
    def test_to_dict_and_retryable(self):
        e = ScannerError(ErrorCode.RATE_LIMITED, "slow", context={"page": 3})
        d = e.to_dict()
        assert d["error_code"] == "RATE_LIMITED"
        assert d["retryable"] is True
        assert d["context"] == {"page": 3}
        assert ScannerError(ErrorCode.ACCESS_DENIED).retryable is False
