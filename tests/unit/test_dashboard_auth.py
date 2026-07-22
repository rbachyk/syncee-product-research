"""Dashboard session-cookie auth (stdlib HMAC tokens)."""

from __future__ import annotations

import time

import pytest

from syncee_scanner.dashboard import auth


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    monkeypatch.delenv("DASHBOARD_SECRET", raising=False)


def test_auth_disabled_without_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    assert auth.auth_enabled() is False
    assert auth.check_credentials("admin", "anything") is False


def test_auth_enabled_with_password(creds):
    assert auth.auth_enabled() is True


def test_token_roundtrip(creds):
    token = auth.issue_token("admin")
    assert auth.verify_token(token) == "admin"


def test_verify_rejects_tampered_token(creds):
    token = auth.issue_token("admin")
    payload, sig = token.rsplit(".", 1)
    assert auth.verify_token(f"{payload}.{sig[:-2]}xx") is None


def test_verify_rejects_wrong_secret(creds, monkeypatch):
    token = auth.issue_token("admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "rotated")  # changes the derived secret
    assert auth.verify_token(token) is None


def test_verify_rejects_expired(creds, monkeypatch):
    token = auth.issue_token("admin")
    monkeypatch.setattr(time, "time", lambda: 10**11)  # far-future clock → token expired
    assert auth.verify_token(token) is None


def test_verify_rejects_garbage(creds):
    assert auth.verify_token(None) is None
    assert auth.verify_token("not-a-token") is None
    assert auth.verify_token("a.b.c") is None


def test_check_credentials(creds):
    assert auth.check_credentials("admin", "s3cret") is True
    assert auth.check_credentials("admin", "wrong") is False
    assert auth.check_credentials("root", "s3cret") is False


def test_safe_next():
    assert auth.safe_next("/products?x=1") == "/products?x=1"
    assert auth.safe_next(None) == "/"
    assert auth.safe_next("//evil.com") == "/"
    assert auth.safe_next("https://evil.com") == "/"
