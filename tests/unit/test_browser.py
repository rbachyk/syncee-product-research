"""Unit tests for browser-pure logic (auth classification, pacing, capture)."""

import random

from syncee_scanner.browser.auth import (
    AuthState,
    _probe_url,
    classify_auth_state,
    markers_from_config,
)
from syncee_scanner.browser.navigation import RateLimitBackoff, polite_delay
from syncee_scanner.browser.network import CapturedResponse, ResponseRecorder
from syncee_scanner.config import load_config


class TestAuthConfig:
    def test_markers_come_from_config(self):
        cfg = load_config()
        cfg.syncee.auth.authenticated_markers = ["DASHBOARD_XYZ"]
        markers = markers_from_config(cfg)
        assert "DASHBOARD_XYZ" in markers.authenticated_text_markers
        assert classify_auth_state("https://syncee.com/", "welcome DASHBOARD_XYZ", markers) \
            is AuthState.AUTHENTICATED

    def test_probe_url_uses_config_path(self):
        cfg = load_config()
        assert _probe_url(cfg) == "https://syncee.com/"
        cfg.syncee.auth.probe_path = "/marketplace"
        assert _probe_url(cfg) == "https://syncee.com/marketplace"
        cfg.syncee.auth.probe_path = "supplier"  # missing leading slash tolerated
        assert _probe_url(cfg) == "https://syncee.com/supplier"


class TestAuthClassification:
    def test_authenticated(self):
        assert classify_auth_state(
            "https://app.syncee.com/marketplace", "Marketplace catalog here"
        ) is AuthState.AUTHENTICATED

    def test_login_by_url(self):
        assert classify_auth_state("https://app.syncee.com/login", "") is AuthState.LOGIN_REQUIRED

    def test_access_denied(self):
        assert classify_auth_state(
            "https://app.syncee.com/403", "Forbidden"
        ) is AuthState.ACCESS_DENIED

    def test_login_by_text(self):
        assert classify_auth_state(
            "https://app.syncee.com/", "Please Sign in with Password"
        ) is AuthState.LOGIN_REQUIRED

    def test_unknown(self):
        assert classify_auth_state("https://x/", "nothing familiar") is AuthState.UNKNOWN


class TestPacing:
    def test_polite_delay_bounds(self):
        calls = []
        rng = random.Random(1)
        d = polite_delay(2.0, 1.0, sleep=calls.append, rng=rng)
        assert 2.0 <= d <= 3.0
        assert calls == [d]

    def test_backoff_grows_and_recovers(self):
        b = RateLimitBackoff(factor=2, max_multiplier=8)
        b.on_rate_limited()
        b.on_rate_limited()
        assert b.scale(1.0) == 4.0
        b.on_rate_limited()
        b.on_rate_limited()  # capped at 8
        assert b.scale(1.0) == 8.0
        b.on_success()
        assert b.scale(1.0) == 4.0


class TestRecorder:
    def test_filter_and_latest(self):
        rec = ResponseRecorder(url_filters=("/api/",))
        rec.record(CapturedResponse("https://x/api/products?page=1", "GET", 200, "xhr", {"a": 1}))
        rec.record(CapturedResponse("https://x/static/app.js", "GET", 200, "script"))
        rec.record(CapturedResponse("https://x/api/products?page=2", "GET", 200, "xhr", {"a": 2}))
        assert len(rec.responses) == 2  # static filtered out
        assert rec.latest("/api/products").body == {"a": 2}
