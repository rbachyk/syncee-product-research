"""FastAPI review/approval dashboard over the Postgres store."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..models import DecisionValue
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
# Approve/reject are audited manual decisions (§14) — mapped to the shared CLI override path.
_DECISIONS = {"approve": DecisionValue.APPROVE, "reject": DecisionValue.REJECT}
_PAGE_LIMIT = 500
_SHIPS_FROM_Q = Query(default=[])  # module-level singleton (avoids ruff B008 in the default)
# Products that failed hard gates or whose supplier was rejected — never "winners".
_VIABLE_SQL = (
    "coalesce(data->>'Review Status','') NOT IN ('Gate Failed', 'Excluded by Supplier')"
)

# Sort key → (label, SQL order expression).
SORTS = {
    "score": ("Score (high→low)", "(data->>'Product Score')::float DESC NULLS LAST"),
    "price": ("Price (high→low)", "(data->>'Proposed Retail Price')::float DESC NULLS LAST"),
    "price_asc": ("Price (low→high)", "(data->>'Proposed Retail Price')::float ASC NULLS LAST"),
    "name": ("Name (A→Z)", "data->>'Product Name' ASC"),
    "enriched": ("Recently enriched", "(data->>'Enriched At') DESC NULLS LAST"),
    "vs_rrp": ("Closest to market (vs RRP ↑)", "(data->>'Price vs RRP')::float ASC NULLS LAST"),
    "vs_rrp_desc": ("Furthest above market (vs RRP ↓)",
                    "(data->>'Price vs RRP')::float DESC NULLS LAST"),
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
    enriched: str = "", ships_from: list[str] = _SHIPS_FROM_Q, supplier: str = "",
    max_vs_rrp: str = "", min_margin: str = "", viable: str = "", q: str = "",
    sort: str = "score", group: str = "none",
):
    """Product gallery: filter (collection/review/selection/enriched/ships-from/supplier/search)."""
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
    ships_from = [s for s in ships_from if s]  # drop empties
    if ships_from:
        where.append("data->>'Ships From' = ANY(%s)")
        params.append(ships_from)
    if supplier:
        # Match products whose supplier's name contains the text (supplier names live on the
        # suppliers row; products only link by id).
        where.append(
            "data->'Supplier'->>0 IN "
            "(SELECT id::text FROM suppliers WHERE data->>'Supplier Name' ILIKE %s)"
        )
        params.append(f"%{supplier}%")
    if _num(max_vs_rrp) is not None:
        # Keep only products priced at most N% above market (negative = below RRP).
        where.append("(data->>'Price vs RRP')::float <= %s")
        params.append(_num(max_vs_rrp))
    if _num(min_margin) is not None:
        where.append("(data->>'Estimated Margin Pct')::float >= %s")
        params.append(_num(min_margin))
    if viable:
        # Exclude products that failed the hard gates or whose supplier was rejected.
        where.append(_VIABLE_SQL)
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
        cur.execute("SELECT data->>'Ships From', count(*) FROM products "
                    "WHERE data->>'Ships From' IS NOT NULL AND data->>'Ships From' <> '' "
                    "GROUP BY 1 ORDER BY 2 DESC")
        ship_countries = [{"name": r[0], "n": r[1]} for r in cur.fetchall()]
        # Counts for the quick-view chips (whole catalogue, ignoring current filters).
        cur.execute(
            "SELECT count(*), "
            "count(*) FILTER (WHERE data->>'Review Status' = 'Shortlisted'), "
            "count(*) FILTER (WHERE data->>'Review Status' = 'Manual Review'), "
            "count(*) FILTER (WHERE data->>'Review Status' = 'Approved'), "
            "count(*) FILTER (WHERE data->>'Selection Status' = 'Initial Assortment Candidate'), "
            "count(*) FILTER (WHERE (data->>'Estimated Margin Pct')::float >= 30 "
            f"  AND (data->>'Price vs RRP')::float <= 10 AND {_VIABLE_SQL}) "
            "FROM products"
        )
        c_all, c_short, c_review, c_appr, c_initial, c_winners = cur.fetchone()
        sup_names = _supplier_names(conn) if group == "supplier" else {}

    plain = not (status or selection or min_margin or max_vs_rrp)
    quick_views = [
        {"label": "All", "query": "", "n": c_all, "active": plain},
        {"label": "★ Winners", "query": "min_margin=30&max_vs_rrp=10&viable=1&sort=vs_rrp",
         "n": c_winners, "active": bool(_num(min_margin))},
        {"label": "Shortlisted", "query": "status=Shortlisted", "n": c_short,
         "active": status == "Shortlisted"},
        {"label": "Needs review", "query": "status=Manual+Review", "n": c_review,
         "active": status == "Manual Review"},
        {"label": "Approved", "query": "status=Approved", "n": c_appr,
         "active": status == "Approved"},
        {"label": "Initial pack", "query": "selection=Initial+Assortment+Candidate", "n": c_initial,
         "active": selection == "Initial Assortment Candidate"},
    ]

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
        "selections": selections, "sorts": SORTS, "groups": GROUPS, "quick_views": quick_views,
        "ship_countries": ship_countries,
        "sel_collection": collection, "sel_status": status, "sel_selection": selection,
        "sel_enriched": enriched, "sel_ships_from": ships_from, "sel_supplier": supplier,
        "sel_max_vs_rrp": max_vs_rrp, "sel_min_margin": min_margin,
        "sel_sort": sort, "sel_group": group, "q": q,
        "shown": len(products), "total": total, "truncated": truncated, "grouped": group != "none",
        "authed": auth.auth_enabled(),
    })


def _actor(request: Request) -> str:
    """Who is making a decision — the logged-in user, for the audit trail."""
    if not auth.auth_enabled():
        return "dashboard"
    user = auth.verify_token(request.cookies.get(auth.COOKIE_NAME))
    return f"dashboard:{user}" if user else "dashboard"


def _apply_review(ids: list[int], action: str, actor: str) -> None:
    """Approve/reject go through the SAME audited path as the CLI (`scoring.overrides`),
    writing a Manual Decisions row per product; shortlist is a plain status change."""
    if not ids:
        return
    decision = _DECISIONS.get(action)
    if decision is not None:
        from ..postgres.persistence import PostgresPersistence
        from ..scoring.overrides import decide_product

        pg = PostgresPersistence(_dsn())
        try:
            with pg._conn.cursor() as cur:
                cur.execute("SELECT id, data FROM products WHERE id = ANY(%s)", (ids,))
                rows = [{"id": r[0], **r[1]} for r in cur.fetchall()]
            for row in rows:
                decide_product(pg, row, decision, decided_by=actor)
        finally:
            pg.close()
        return
    new_status = REVIEW_ACTIONS.get(action)
    if new_status:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET data = data || %s WHERE id = ANY(%s)",
                (psycopg.types.json.Jsonb({"Review Status": new_status}), ids),
            )


@app.post("/product/{pid}/review")
def review(request: Request, pid: int, action: str = Form(...), redirect: str = Form("/")):
    """Approve / reject (audited) / shortlist a single product."""
    _apply_review([pid], action, _actor(request))
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
def bulk_review(request: Request, action: str = Form(...), pid: list[int] = _PID_LIST,
                redirect: str = Form("/")):
    """Approve / reject (audited) / shortlist many products at once."""
    _apply_review(pid, action, _actor(request))
    return RedirectResponse(auth.safe_next(redirect), status_code=303)


# Curated field groups for the detail page (label, product-field key).
_DETAIL_SECTIONS = [
    ("Pricing & margin", [
        ("Final price (EUR)", "Proposed Retail Price"),
        ("Market price / RRP (EUR)", "Market Price (EUR)"),
        ("Price vs RRP (%)", "Price vs RRP"),
        ("Supplier price (source ccy)", "Supplier Price"), ("Currency", "Currency"),
        ("Syncee RRP (source ccy)", "Suggested Retail Price"),
        ("Shipping cost", "Shipping Cost"), ("Shipping cost known", "Shipping Cost Known"),
        ("Landed cost (EUR)", "Estimated Landed Cost"),
        ("Margin amount (EUR)", "Estimated Margin Amount"), ("Margin %", "Estimated Margin Pct"),
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
    from ..config import load_config

    m = load_config().margin
    pricing = {"mode": m.pricing_mode, "target_margin": m.target_margin_pct,
               "markup": m.markup_multiple, "min_margin": m.minimum_margin_pct,
               "modes": ["rrp", "target_margin", "markup"]}
    return _TEMPLATES.TemplateResponse(request, "control.html", {
        "stats": _stats(), "active": jobs.active_job(), "recent": jobs.recent_jobs(),
        "collections": COLLECTIONS, "pricing": pricing, "authed": auth.auth_enabled(),
    })


@app.get("/export/{what}")
def export_csv(what: str):
    """Stream a CSV export (suppliers / products / candidates) generated from Postgres."""
    import shutil
    import tempfile

    from starlette.background import BackgroundTask

    from ..export import service as export_service
    from ..postgres.persistence import PostgresPersistence

    exporters = {
        "suppliers": export_service.export_suppliers,
        "products": export_service.export_products,
        "candidates": export_service.export_candidates,
    }
    fn = exporters.get(what)
    if not fn:
        return Response(status_code=404)
    tmp = tempfile.mkdtemp(prefix="rbexport-")
    pg = PostgresPersistence(_dsn())
    try:
        path = fn(pg, out_dir=tmp)[0]
    finally:
        pg.close()
    from fastapi.responses import FileResponse
    return FileResponse(
        path, media_type="text/csv", filename=path.name,
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )


@app.get("/insights", response_class=HTMLResponse)
def insights(request: Request):
    """Sourcing diagnostics: is there a profitable pool at market prices? (all literal SQL —
    the 'Estimated Margin Pct' key contains a %, which breaks parameterized queries)."""
    num = "~ '^-?[0-9.]+$'"  # numeric-only guard so ::float never errors on bad data
    with _conn() as conn, conn.cursor() as cur:
        marg = "(data->>'Estimated Margin Pct')::float"
        vrrp = "(data->>'Price vs RRP')::float"
        cur.execute(
            "SELECT count(*) FILTER (WHERE m < 0), count(*) FILTER (WHERE m >= 0 AND m < 30), "
            "count(*) FILTER (WHERE m >= 30 AND m < 40), "
            "count(*) FILTER (WHERE m >= 40 AND m < 50), "
            "count(*) FILTER (WHERE m >= 50), count(*) "
            f"FROM (SELECT {marg} m FROM products WHERE data->>'Estimated Margin Pct' {num}) t"
        )
        mk = ["loss", "thin", "moderate", "good", "great", "total"]
        margin = dict(zip(mk, cur.fetchone(), strict=True))
        cur.execute(
            "SELECT count(*) FILTER (WHERE v <= 0), count(*) FILTER (WHERE v > 0 AND v <= 10), "
            "count(*) FILTER (WHERE v > 10 AND v <= 25), "
            "count(*) FILTER (WHERE v > 25 AND v <= 50), "
            "count(*) FILTER (WHERE v > 50), count(*) "
            f"FROM (SELECT {vrrp} v FROM products WHERE data->>'Price vs RRP' {num}) t"
        )
        vk = ["below", "p10", "p25", "p50", "over", "total"]
        vsrrp = dict(zip(vk, cur.fetchone(), strict=True))
        # Winners = healthy margin AND priced at/near market, per collection.
        cur.execute(
            "SELECT coalesce(data->>'Collection','(unclassified)'), "
            f"count(*) FILTER (WHERE {marg} >= 40 AND {vrrp} <= 10 AND {_VIABLE_SQL}), count(*) "
            f"FROM products WHERE data->>'Estimated Margin Pct' {num} "
            "GROUP BY 1 ORDER BY 2 DESC"
        )
        by_coll = [{"name": r[0], "winners": r[1], "total": r[2]} for r in cur.fetchall()]
        # Best competitively-priced candidates.
        cur.execute(
            "SELECT id, data->>'Product Name', data->>'Collection', data->>'Estimated Margin Pct', "
            "data->>'Price vs RRP', data->>'Proposed Retail Price' FROM products "
            f"WHERE data->>'Estimated Margin Pct' {num} AND data->>'Price vs RRP' {num} "
            f"AND {vrrp} <= 10 AND {marg} >= 30 AND {_VIABLE_SQL} ORDER BY {marg} DESC LIMIT 40"
        )
        cols = ["id", "name", "collection", "margin", "vs_rrp", "price"]
        top = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    winners_total = sum(c["winners"] for c in by_coll)
    return _TEMPLATES.TemplateResponse(request, "insights.html", {
        "margin": margin, "vsrrp": vsrrp, "by_coll": by_coll, "top": top,
        "winners_total": winners_total, "authed": auth.auth_enabled(),
    })


@app.get("/runs", response_class=HTMLResponse)
def runs_list(request: Request):
    """Scan-run history (spec §12)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_id, data, checkpoint IS NOT NULL "
            "FROM scan_runs ORDER BY id DESC LIMIT 100"
        )
        rows = cur.fetchall()
    runs = [{"run_id": rid, "resumable": hc, **(data or {})} for rid, data, hc in rows]
    return _TEMPLATES.TemplateResponse(request, "runs.html", {
        "runs": runs, "authed": auth.auth_enabled(),
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


def _num(v: str) -> float | None:
    try:
        return float(v) if v.strip() != "" else None
    except (ValueError, AttributeError):
        return None


@app.post("/jobs/score")
def start_score(target: str = Form(...), pricing_mode: str = Form(""),
                target_margin: str = Form(""), markup: str = Form(""), min_margin: str = Form("")):
    """Score suppliers/products, with optional pricing overrides for a re-score."""
    argv = jobs.score_argv(
        target, pricing_mode=pricing_mode.strip() or None,
        target_margin=_num(target_margin), markup=_num(markup), min_margin=_num(min_margin),
    )
    if argv is None:
        return RedirectResponse("/control?error=Unknown+score+target", status_code=303)
    params = {"target": target, "pricing_mode": pricing_mode.strip(),
              "target_margin": _num(target_margin), "markup": _num(markup),
              "min_margin": _num(min_margin)}
    _, err = jobs.start_job(f"score-{target}", argv, params)
    return RedirectResponse(f"/control?error={err}" if err else "/control", status_code=303)


def _launch(kind: str, argv: list[str] | None, params: dict) -> RedirectResponse:
    if argv is None:
        return RedirectResponse("/control?error=Invalid+request", status_code=303)
    _, err = jobs.start_job(kind, argv, params)
    return RedirectResponse(f"/control?error={err}" if err else "/control", status_code=303)


@app.post("/jobs/select")
def start_select(target: str = Form(...)):
    """Build the assortment: target = 'initial' | 'new-arrivals'."""
    return _launch(f"select-{target}", jobs.select_argv(target), {"target": target})


@app.post("/jobs/prep")
def start_prep(limit: str = Form("")):
    """Publish-prep the assortment (SEO copy + generative images via OpenRouter)."""
    lim = int(limit) if limit.strip().isdigit() else None
    return _launch("publish-prep", jobs.prep_argv(lim), {"limit": lim})


@app.post("/jobs/push")
def start_push(apply: str = Form(""), key: str = Form("")):
    """Push to Shopify — dry-run unless 'apply' is checked."""
    do_apply = bool(apply)
    argv = jobs.push_argv(apply=do_apply, key=key.strip() or None)
    return _launch("shopify-push", argv, {"apply": do_apply, "key": key.strip()})


@app.post("/jobs/validate")
def start_validate():
    """Check the saved Syncee session is still valid."""
    return _launch("auth-validate", jobs.validate_argv(), {})


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
