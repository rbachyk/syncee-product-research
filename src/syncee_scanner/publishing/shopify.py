"""Shopify Admin API — OAuth token acquisition (publish-prep step 5).

New Shopify apps (2026) are Dev Dashboard apps: no direct ``shpat_`` token, only a client id +
secret used via OAuth. This runs the one-time authorization-code flow to mint a permanent
(offline) Admin API access token, which is then saved to ``.env`` and reused by every product
push. Credentials come from the environment, never YAML.

``authorize_url`` / ``verify_hmac`` are pure and unit-tested; ``exchange_code`` and
``run_oauth_flow`` are the live pieces (a loopback HTTP server catches Shopify's redirect).
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import os
import threading
import urllib.parse
from dataclasses import dataclass

import httpx

from ..observability.errors import ErrorCode, ScannerError

DEFAULT_SCOPES = "write_products,read_products"
DEFAULT_REDIRECT_PORT = 3456
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_REDIRECT_PORT}/callback"


@dataclass
class ShopifyCreds:
    shop: str
    client_id: str
    client_secret: str
    token: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ShopifyCreds:
        env = env or dict(os.environ)
        vals = {
            "SHOPIFY_STORE_DOMAIN": env.get("SHOPIFY_STORE_DOMAIN"),
            "SHOPIFY_CLIENT_ID": env.get("SHOPIFY_CLIENT_ID"),
            "SHOPIFY_CLIENT_SECRET": env.get("SHOPIFY_CLIENT_SECRET"),
        }
        missing = [k for k, v in vals.items() if not v]
        if missing:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR, "Missing Shopify env: " + ", ".join(missing)
            )
        return cls(
            shop=vals["SHOPIFY_STORE_DOMAIN"].strip(),
            client_id=vals["SHOPIFY_CLIENT_ID"].strip(),
            client_secret=vals["SHOPIFY_CLIENT_SECRET"].strip(),
            token=(env.get("SHOPIFY_ADMIN_TOKEN") or "").strip() or None,
        )


def authorize_url(creds: ShopifyCreds, *, redirect_uri: str, state: str,
                  scopes: str = DEFAULT_SCOPES) -> str:
    """Build the Shopify OAuth authorize URL (offline/permanent token — no grant_options)."""
    q = urllib.parse.urlencode({
        "client_id": creds.client_id,
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"https://{creds.shop}/admin/oauth/authorize?{q}"


def verify_hmac(params: dict[str, str], secret: str) -> bool:
    """Verify Shopify's callback HMAC (all params except hmac, sorted, joined, SHA256)."""
    provided = params.get("hmac", "")
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hmac")
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return bool(provided) and hmac.compare_digest(digest, provided)


def exchange_code(creds: ShopifyCreds, code: str) -> str:
    """Exchange an authorization code for a permanent Admin API access token."""
    r = httpx.post(
        f"https://{creds.shop}/admin/oauth/access_token",
        json={"client_id": creds.client_id, "client_secret": creds.client_secret, "code": code},
        timeout=30,
    )
    if r.status_code != 200:
        raise ScannerError(
            ErrorCode.CONFIGURATION_ERROR,
            f"Shopify token exchange failed: {r.status_code} {r.text[:200]}",
        )
    token = r.json().get("access_token")
    if not token:
        raise ScannerError(ErrorCode.CONFIGURATION_ERROR, "No access_token in Shopify response.")
    return token


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}
    done: threading.Event = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        _CallbackHandler.result = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>RB Home &times; Shopify connected.</h2>"
            b"<p>Authorization received. You can close this tab and return to the terminal.</p>"
        )
        _CallbackHandler.done.set()

    def log_message(self, *args) -> None:  # silence server logging
        pass


def run_oauth_flow(creds: ShopifyCreds, *, redirect_uri: str = DEFAULT_REDIRECT_URI,
                   scopes: str = DEFAULT_SCOPES, open_browser=None, state: str | None = None,
                   timeout: float = 300.0) -> str:
    """Interactive: serve the loopback callback, open the authorize URL, return the token."""
    import secrets as _secrets
    import webbrowser

    state = state or _secrets.token_urlsafe(16)
    port = urllib.parse.urlparse(redirect_uri).port or DEFAULT_REDIRECT_PORT
    _CallbackHandler.result = {}
    _CallbackHandler.done = threading.Event()

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = authorize_url(creds, redirect_uri=redirect_uri, state=state, scopes=scopes)
    (open_browser or webbrowser.open)(url)
    print(f"\nOpen this URL in your browser if it didn't open automatically:\n{url}\n")

    got = _CallbackHandler.done.wait(timeout=timeout)
    server.shutdown()
    if not got:
        raise ScannerError(ErrorCode.CONFIGURATION_ERROR, "Timed out waiting for Shopify approval.")

    params = _CallbackHandler.result
    if params.get("state") != state:
        raise ScannerError(ErrorCode.CONFIGURATION_ERROR, "OAuth state mismatch — aborting.")
    if not verify_hmac(params, creds.client_secret):
        raise ScannerError(
            ErrorCode.CONFIGURATION_ERROR, "OAuth HMAC verification failed — aborting."
        )
    return exchange_code(creds, params["code"])


def upsert_env(path: str, key: str, value: str) -> None:
    """Insert or update ``key=value`` in a .env file (leaves other lines untouched)."""
    lines: list[str] = []
    found = False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --- Admin API client + product push -----------------------------------------------


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace("&", "and").replace("  ", " ")


class ShopifyClient:
    """Minimal Shopify Admin API client for the publish-prep push."""

    def __init__(self, creds: ShopifyCreds, *, api_version: str = "2026-07", timeout: float = 30.0):
        if not creds.token:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                "SHOPIFY_ADMIN_TOKEN not set — run `syncee-scanner shopify auth` first.",
            )
        self.base = f"https://{creds.shop}/admin/api/{api_version}"
        self._c = httpx.Client(
            headers={"X-Shopify-Access-Token": creds.token, "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._c.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        r = self._c.request(method, f"{self.base}{path}", **kw)
        if r.status_code >= 400:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                f"Shopify {method} {path} → {r.status_code}: {r.text[:200]}",
            )
        return r

    def iter_products(self) -> list[dict]:
        """All products (follows cursor pagination via the Link header)."""
        out: list[dict] = []
        fields = "id,title,handle,vendor,product_type,tags,variants,images"
        url = f"/products.json?limit=250&fields={fields}"
        while url:
            r = self._req("GET", url)
            out.extend(r.json().get("products", []))
            nxt = None
            for part in r.headers.get("Link", "").split(","):
                if 'rel="next"' in part:
                    nxt = part[part.find("<") + 1 : part.find(">")]
            url = nxt.replace(self.base, "") if nxt else None
        return out

    def update_product(self, product_id: int, fields: dict) -> None:
        body = {"product": {"id": product_id, **fields}}
        self._req("PUT", f"/products/{product_id}.json", json=body)

    def set_seo(self, product_id: int, seo_title: str | None, meta_description: str | None) -> None:
        """SEO title/description are the global.title_tag / description_tag metafields."""
        for key, value in (("title_tag", seo_title), ("description_tag", meta_description)):
            if not value:
                continue
            self._req("POST", f"/products/{product_id}/metafields.json", json={"metafield": {
                "namespace": "global", "key": key,
                "type": "single_line_text_field", "value": value,
            }})

    def add_image(self, product_id: int, image_bytes: bytes, alt: str | None) -> None:
        import base64
        self._req("POST", f"/products/{product_id}/images.json", json={"image": {
            "attachment": base64.b64encode(image_bytes).decode(),
            "alt": alt or "", "position": 1,  # position 1 = primary image
        }})


def index_shopify(products: list[dict]) -> dict[str, list[dict]]:
    """Index Shopify products by SKU and by barcode for matching."""
    idx: dict[str, list[dict]] = {}
    for p in products:
        for v in p.get("variants") or []:
            for key in (f"sku:{_norm(v.get('sku'))}", f"barcode:{_norm(v.get('barcode'))}"):
                if key not in ("sku:", "barcode:"):
                    idx.setdefault(key, []).append(p)
    return idx


def match_row(row: dict, index: dict[str, list[dict]]) -> list[dict]:
    """Find the Shopify product(s) for a Baserow row — by barcode, then SKU (+vendor)."""
    import json as _json
    raw = row.get("Raw Data")
    barcode = None
    if isinstance(raw, str) and raw:
        try:
            v = (_json.loads(raw).get("variants") or [{}])[0]
            barcode = v.get("barcode") or v.get("UPC") or v.get("upc")
        except _json.JSONDecodeError:
            pass
    if barcode and f"barcode:{_norm(barcode)}" in index:
        return _dedupe(index[f"barcode:{_norm(barcode)}"])
    sku = row.get("Supplier SKU")
    if sku and f"sku:{_norm(sku)}" in index:
        return _dedupe(index[f"sku:{_norm(sku)}"])
    return []


def _dedupe(products: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in products:
        if p["id"] not in seen:
            seen.add(p["id"])
            out.append(p)
    return out


def _merge_tags(existing: str, new: str) -> str:
    """Append our publish tags to Shopify's existing tags (keep Syncee's, de-dupe)."""
    have = [t.strip() for t in (existing or "").split(",") if t.strip()]
    lower = {t.lower() for t in have}
    for t in (new or "").split(","):
        t = t.strip()
        if t and t.lower() not in lower:
            have.append(t)
            lower.add(t.lower())
    return ", ".join(have)


def push_products(persistence, client: ShopifyClient, config, *,
                  dry_run: bool = True, keys: list[str] | None = None) -> list[dict]:
    """Match each Ready-to-Publish product to its Shopify listing and push copy/SEO/image.

    Only touches title, body, handle, product type, tags (appended), SEO title/description, and
    adds the processed image as the primary — never vendor or variants, so Syncee's fulfillment
    link stays intact. ``dry_run`` reports the plan without writing.
    """
    index = index_shopify(client.iter_products())
    cands = [r for r in persistence.iter_products()
             if r.get("Selection Status") == "Initial Assortment Candidate"]
    if keys:
        want = set(keys)
        cands = [r for r in cands if r.get("Product Key") in want]

    results: list[dict] = []
    for row in cands:
        matches = match_row(row, index)
        res: dict = {"name": row.get("Product Name"), "matches": len(matches),
                     "shopify_ids": [m["id"] for m in matches]}
        if not matches:
            res["status"] = "not-imported"
            results.append(res)
            continue

        fields = {k: v for k, v in {
            "title": row.get("Cleaned Title") or row.get("Product Name"),
            "body_html": row.get("Description HTML"),
            "handle": row.get("Handle"),
            "product_type": row.get("Product Type"),
        }.items() if v}
        pf = row.get("Processed Image") or []
        img_url = pf[0].get("url") if pf else None
        res.update({
            "status": "would-push" if dry_run else "pushed",
            "new_title": fields.get("title"),
            "seo": bool(row.get("SEO Title")),
            "image": bool(img_url),
            "fields": list(fields),
        })

        our_alt = (row.get("Image Alt Text") or "").strip()
        if not dry_run:
            img_bytes = None
            if img_url:
                img_bytes = httpx.get(img_url, timeout=30, follow_redirects=True).content
            for m in matches:
                # Tags: replace with our meaningful tags — don't carry over Syncee's import clutter.
                client.update_product(m["id"], {**fields, "tags": row.get("Publish Tags") or ""})
                client.set_seo(m["id"], row.get("SEO Title"), row.get("Meta Description"))
                # Image: idempotent — skip if our processed image (by alt) is already attached.
                already = any((im.get("alt") or "").strip() == our_alt
                              for im in (m.get("images") or []))
                if img_bytes and not already:
                    client.add_image(m["id"], img_bytes, our_alt)
        results.append(res)
    return results
