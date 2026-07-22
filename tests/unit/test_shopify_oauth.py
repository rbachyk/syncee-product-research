"""Unit tests for Shopify OAuth helpers (pure parts)."""

import hashlib
import hmac

import pytest

from syncee_scanner.observability.errors import ScannerError
from syncee_scanner.publishing.shopify import (
    ShopifyCreds,
    authorize_url,
    upsert_env,
    verify_hmac,
)

ENV = {
    "SHOPIFY_STORE_DOMAIN": "ewcvk1-20.myshopify.com",
    "SHOPIFY_CLIENT_ID": "abc123",
    "SHOPIFY_CLIENT_SECRET": "s3cret",
}


def test_creds_from_env_and_missing():
    c = ShopifyCreds.from_env(ENV)
    assert c.shop == "ewcvk1-20.myshopify.com" and c.token is None
    with pytest.raises(ScannerError):
        ShopifyCreds.from_env({"SHOPIFY_STORE_DOMAIN": "x"})


def test_authorize_url():
    url = authorize_url(ShopifyCreds.from_env(ENV), redirect_uri="http://localhost:3456/callback",
                        state="nonce1", scopes="write_products,read_products")
    assert url.startswith("https://ewcvk1-20.myshopify.com/admin/oauth/authorize?")
    assert "client_id=abc123" in url and "state=nonce1" in url
    assert "scope=write_products%2Cread_products" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A3456%2Fcallback" in url


def test_verify_hmac_roundtrip():
    params = {"code": "xyz", "shop": "ewcvk1-20.myshopify.com", "state": "n", "timestamp": "1"}
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    good = hmac.new(b"s3cret", message.encode(), hashlib.sha256).hexdigest()
    assert verify_hmac({**params, "hmac": good}, "s3cret")
    assert not verify_hmac({**params, "hmac": "deadbeef"}, "s3cret")
    assert not verify_hmac(params, "s3cret")  # missing hmac


def test_upsert_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("EXISTING=1\nSHOPIFY_ADMIN_TOKEN=old\n")
    upsert_env(str(p), "SHOPIFY_ADMIN_TOKEN", "new")
    upsert_env(str(p), "SHOPIFY_STORE_DOMAIN", "shop.myshopify.com")
    text = p.read_text()
    assert "SHOPIFY_ADMIN_TOKEN=new" in text and "old" not in text
    assert "SHOPIFY_STORE_DOMAIN=shop.myshopify.com" in text
    assert "EXISTING=1" in text
