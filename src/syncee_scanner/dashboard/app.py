"""FastAPI review/approval dashboard over the Postgres store."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..postgres.persistence import SCHEMA
from . import auth, jobs

app = FastAPI(title="RB Home — Product Review")
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Paths reachable without a session (login form + healthcheck).
_PUBLIC_PATHS = {"/login", "/health"}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    """Gate every route behind a valid session cookie when a password is configured."""
    if not auth.auth_enabled() or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    if request.method == "GET":
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return Response("Unauthorized", status_code=401)


@app.on_event("startup")
def _ensure_schema() -> None:
    """Create the tables if they don't exist yet, so the dashboard works on a fresh DB
    (before the first scan) instead of 500-ing on missing tables."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
    jobs.reconcile_stale()  # any job left 'running' by a prior process is dead → mark failed


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    if auth.auth_enabled() and auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return _TEMPLATES.TemplateResponse(
        request, "login.html", {"next": auth.safe_next(next), "error": error}
    )


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    target = auth.safe_next(next)
    if not auth.check_credentials(username, password):
        return RedirectResponse(f"/login?next={target}&error=Invalid+credentials", status_code=303)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.issue_token(username),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=auth.cookie_secure(), path="/",
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp

COLLECTIONS = ["Kitchen Convenience", "Dining", "Home Comfort", "Bathroom"]
REVIEW_ACTIONS = {
    "approve": "Approved",
    "reject": "Manually Rejected",
    "shortlist": "Shortlisted",  # send back to review pool
}
_PAGE_LIMIT = 500

# Sort key → (label, SQL order expression).
SORTS = {
    "score": ("Score (high→low)", "(data->>'Product Score')::float DESC NULLS LAST"),
    "price": ("Price (high→low)", "(data->>'Proposed Retail Price')::float DESC NULLS LAST"),
    "price_asc": ("Price (low→high)", "(data->>'Proposed Retail Price')::float ASC NULLS LAST"),
    "name": ("Name (A→Z)", "data->>'Product Name' ASC"),
    "enriched": ("Recently enriched", "(data->>'Enriched At') DESC NULLS LAST"),
}
# Group key → (label, SQL group expression, product field to read the group value from).
GROUPS = {
    "none": ("No grouping", None, None),
    "collection": ("Collection", "data->>'Collection'", "Collection"),
    "review": ("Review status", "data->>'Review Status'", "Review Status"),
    "selection": ("Selection status", "data->>'Selection Status'", "Selection Status"),
    "supplier": ("Supplier", "data->'Supplier'->>0", "Supplier"),
}


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return dsn


def _conn() -> psycopg.Connection:
    return psycopg.connect(_dsn(), autocommit=True)


def _supplier_names(conn) -> dict[str, str]:
    """Map supplier row id (as string) → supplier name, for supplier grouping/labels."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, data->>'Supplier Name' FROM suppliers")
        return {str(rid): (name or f"Supplier {rid}") for rid, name in cur.fetchall()}


@app.get("/", response_class=HTMLResponse)
def gallery(
    request: Request, collection: str = "", status: str = "", selection: str = "",
    enriched: str = "", q: str = "", sort: str = "score", group: str = "none",
):
    """Product gallery: filter (collection/review/selection/enriched/search), sort, and group."""
    sort = sort if sort in SORTS else "score"
    group = group if group in GROUPS else "none"
    where, params = ["TRUE"], []
    if collection:
        where.append("data->>'Collection' = %s")
        params.append(collection)
    if status:
        where.append("data->>'Review Status' = %s")
        params.append(status)
    if selection:
        where.append("data->>'Selection Status' = %s")
        params.append(selection)
    if enriched == "yes":
        where.append("data->>'Enriched At' IS NOT NULL")
    elif enriched == "no":
        where.append("data->>'Enriched At' IS NULL")
    if q:
        where.append("data->>'Product Name' ILIKE %s")
        params.append(f"%{q}%")

    group_expr, group_field = GROUPS[group][1], GROUPS[group][2]
    order = (f"{group_expr} ASC NULLS LAST, " if group_expr else "") + SORTS[sort][1]
    sql = (
        "SELECT id, data FROM products WHERE " + " AND ".join(where)
        + f" ORDER BY {order} LIMIT {_PAGE_LIMIT + 1}"
    )
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM products WHERE " + " AND ".join(where), params)
        total = cur.fetchone()[0]
        cur.execute("SELECT DISTINCT data->>'Review Status' FROM products "
                    "WHERE data->>'Review Status' IS NOT NULL ORDER BY 1")
        statuses = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT data->>'Selection Status' FROM products "
                    "WHERE data->>'Selection Status' IS NOT NULL ORDER BY 1")
        selections = [r[0] for r in cur.fetchall()]
        sup_names = _supplier_names(conn) if group == "supplier" else {}

    truncated = len(rows) > _PAGE_LIMIT
    rows = rows[:_PAGE_LIMIT]
    products = [{"id": rid, **d} for rid, d in rows]
    # Attach the group label each card falls under (template emits a header on change).
    if group != "none":
        for p in products:
            val = p.get(group_field)
            if group == "supplier":
                sid = (val or [None])[0]
                label = sup_names.get(str(sid), "— unknown —") if sid else "— no supplier —"
            else:
                label = val or f"— no {GROUPS[group][0].lower()} —"
            p["_group"] = label

    return _TEMPLATES.TemplateResponse(request, "gallery.html", {
        "products": products, "collections": COLLECTIONS, "statuses": statuses,
        "selections": selections, "sorts": SORTS, "groups": GROUPS,
        "sel_collection": collection, "sel_status": status, "sel_selection": selection,
        "sel_enriched": enriched, "sel_sort": sort, "sel_group": group, "q": q,
        "shown": len(products), "total": total, "truncated": truncated, "grouped": group != "none",
        "authed": auth.auth_enabled(),
    })


@app.post("/product/{pid}/review")
def review(pid: int, action: str = Form(...), redirect: str = Form("/")):
    """Approve / reject / send-back a product (writes Review Status)."""
    new_status = REVIEW_ACTIONS.get(action)
    if new_status:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET data = data || %s WHERE id = %s",
                (psycopg.types.json.Jsonb({"Review Status": new_status}), pid),
            )
    return RedirectResponse(redirect or "/", status_code=303)


@app.post("/product/{pid}/select")
def select(pid: int, action: str = Form(...), redirect: str = Form("/")):
    """Pin into / remove from the assortment (writes Selection Status)."""
    status = ("Initial Assortment Candidate" if action == "pin" else "Not Selected")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE products SET data = data || %s WHERE id = %s",
            (psycopg.types.json.Jsonb({"Selection Status": status}), pid),
        )
    return RedirectResponse(redirect or "/", status_code=303)


_PID_LIST = Form(())  # module-level singleton (avoids a call in the default → ruff B008)


@app.post("/products/bulk")
def bulk_review(action: str = Form(...), pid: list[int] = _PID_LIST,
                redirect: str = Form("/")):
    """Approve / reject / shortlist many products at once."""
    new_status = REVIEW_ACTIONS.get(action)
    if new_status and pid:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET data = data || %s WHERE id = ANY(%s)",
                (psycopg.types.json.Jsonb({"Review Status": new_status}), pid),
            )
    return RedirectResponse(auth.safe_next(redirect), status_code=303)


# Curated field groups for the detail page (label, product-field key).
_DETAIL_SECTIONS = [
    ("Pricing & margin", [
        ("Supplier price", "Supplier Price"), ("Currency", "Currency"),
        ("Suggested retail", "Suggested Retail Price"),
        ("Proposed retail", "Proposed Retail Price"),
        ("Shipping cost", "Shipping Cost"), ("Shipping cost known", "Shipping Cost Known"),
        ("Landed cost", "Estimated Landed Cost"),
        ("Margin amount", "Estimated Margin Amount"), ("Margin %", "Estimated Margin %"),
    ]),
    ("Shipping & stock", [
        ("Ships from", "Ships From"), ("Dispatch min days", "Shipping Min Days"),
        ("Dispatch max days", "Shipping Max Days"), ("Stock status", "Stock Status"),
        ("Stock quantity", "Stock Quantity"), ("Variants", "Variants Count"),
    ]),
    ("Classification & scoring", [
        ("Collection", "Collection"), ("Product score", "Product Score"),
        ("Score version", "Product Score Version"),
        ("Classification confidence", "Classification Confidence"),
        ("Reason codes", "Reason Codes"), ("Exclusion codes", "Exclusion Reason Codes"),
        ("Risk flags", "Risk Flags"),
    ]),
    ("Publish content", [
        ("Cleaned title", "Cleaned Title"), ("SEO title", "SEO Title"),
        ("Meta description", "Meta Description"), ("Handle", "Handle"),
        ("Tags", "Publish Tags"), ("Image alt", "Image Alt Text"),
        ("Content angle", "Content Angle"), ("Publish-prep status", "Publish-Prep Status"),
    ]),
    ("Source & timeline", [
        ("Syncee product ID", "Syncee Product ID"), ("Product URL", "Product URL"),
        ("Brand", "Brand"), ("Syncee category", "Syncee Category"),
        ("First seen", "First Seen At"), ("Last seen", "Last Seen At"),
        ("Enriched at", "Enriched At"), ("Syncee updated", "Syncee Updated At"),
    ]),
]


@app.get("/product/{pid}", response_class=HTMLResponse)
def product_detail(request: Request, pid: int):
    """Everything stored for one product: images, copy, pricing, variants, supplier, raw."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, data FROM products WHERE id = %s", (pid,))
        row = cur.fetchone()
        if not row:
            return Response(status_code=404)
        p = {"id": row[0], **row[1]}
        supplier = None
        link = p.get("Supplier") or []
        if link:
            cur.execute("SELECT id, data FROM suppliers WHERE id = %s", (link[0],))
            s = cur.fetchone()
            if s:
                supplier = {"id": s[0], **s[1]}
    try:
        raw = json.loads(p.get("Raw Data") or "{}")
    except (ValueError, TypeError):
        raw = {}
    variants = [v for v in (raw.get("variants") or []) if isinstance(v, dict)]
    vkeys: list[str] = []
    for v in variants:
        for k in v:
            if k not in vkeys:
                vkeys.append(k)
    images = [i.strip() for i in (p.get("Image URLs") or "").split("\n") if i.strip()]
    if not images and p.get("Main Image URL"):
        images = [p["Main Image URL"]]
    processed = (p.get("Processed Image") or [{}])[0].get("url")
    back = auth.safe_next(request.query_params.get("back") or "/")
    return _TEMPLATES.TemplateResponse(request, "detail.html", {
        "p": p, "supplier": supplier, "variants": variants, "vkeys": vkeys,
        "images": images, "processed": processed, "sections": _DETAIL_SECTIONS,
        "raw_json": json.dumps(raw, indent=2, ensure_ascii=False) if raw else "",
        "back": back, "authed": auth.auth_enabled(),
    })


@app.get("/asset/{asset_id}")
def asset(asset_id: int):
    """Serve a processed product image stored in Postgres."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT content_type, content FROM product_assets WHERE id = %s", (asset_id,))
        row = cur.fetchone()
    if not row:
        return Response(status_code=404)
    return Response(content=bytes(row[1]), media_type=row[0])


@app.get("/health")
def health():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM products")
        return {"status": "ok", "products": cur.fetchone()[0]}


# --- Operations console: download/enrich control + status --------------------------

def _stats() -> dict:
    """Catalogue + pipeline progress: totals, per-collection, review mix, latest scan run."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), "
            "count(*) FILTER (WHERE data->>'Enriched At' IS NOT NULL), "
            "count(*) FILTER (WHERE data->>'Product Score' IS NOT NULL) FROM products"
        )
        total, enriched, scored = cur.fetchone()
        cur.execute("SELECT count(*) FROM suppliers")
        suppliers = cur.fetchone()[0]
        cur.execute(
            "SELECT coalesce(data->>'Collection', '(unclassified)'), count(*), "
            "count(*) FILTER (WHERE data->>'Enriched At' IS NOT NULL), "
            "count(*) FILTER (WHERE data->>'Product Score' IS NOT NULL) "
            "FROM products GROUP BY 1 ORDER BY 2 DESC"
        )
        by_collection = [
            {"name": n, "total": t, "enriched": e, "scored": s} for n, t, e, s in cur.fetchall()
        ]
        cur.execute(
            "SELECT coalesce(data->>'Review Status', '(none)'), count(*) "
            "FROM products GROUP BY 1 ORDER BY 2 DESC"
        )
        by_review = [{"name": n, "count": c} for n, c in cur.fetchall()]
        cur.execute("SELECT data FROM scan_runs ORDER BY id DESC LIMIT 1")
        run_row = cur.fetchone()
    last_run = (run_row[0] or {}) if run_row else None
    return {
        "total": total, "enriched": enriched, "scored": scored, "suppliers": suppliers,
        "unenriched": total - enriched, "by_collection": by_collection,
        "by_review": by_review, "last_run": last_run,
    }


@app.get("/control", response_class=HTMLResponse)
def control(request: Request):
    return _TEMPLATES.TemplateResponse(request, "control.html", {
        "stats": _stats(), "active": jobs.active_job(), "recent": jobs.recent_jobs(),
        "collections": COLLECTIONS, "authed": auth.auth_enabled(),
    })


@app.post("/jobs/scan")
def start_scan(category: str = Form("")):
    _, err = jobs.start_job("scan", jobs.scan_argv(category.strip() or None),
                            {"category": category.strip()})
    return RedirectResponse(f"/control?error={err}" if err else "/control", status_code=303)


@app.post("/jobs/enrich")
def start_enrich(limit: str = Form(""), collection: str = Form(""),
                 reenrich: str = Form("")):
    try:
        lim = int(limit) if limit.strip() else None
    except ValueError:
        lim = None
    argv = jobs.enrich_argv(lim, reenrich=bool(reenrich), collection=collection.strip() or None)
    params = {"limit": lim, "collection": collection.strip(), "reenrich": bool(reenrich)}
    _, err = jobs.start_job("enrich", argv, params)
    return RedirectResponse(f"/control?error={err}" if err else "/control", status_code=303)


_JOB_ACTIONS = {"pause": jobs.pause_job, "resume": jobs.resume_job, "cancel": jobs.cancel_job}


@app.post("/jobs/{job_id}/{action}")
def job_action(job_id: int, action: str, redirect: str = Form("/control")):
    """Pause / resume / cancel a job (signals its process)."""
    fn = _JOB_ACTIONS.get(action)
    if not fn:
        return Response(status_code=404)
    err = fn(job_id)
    target = auth.safe_next(redirect)
    return RedirectResponse(f"{target}?error={err}" if err else target, status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    job = jobs.get_job(job_id)
    if not job:
        return Response(status_code=404)
    return _TEMPLATES.TemplateResponse(request, "job.html", {
        "job": job, "authed": auth.auth_enabled(),
    })
