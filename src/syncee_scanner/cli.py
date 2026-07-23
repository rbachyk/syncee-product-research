"""Command-line interface (spec §33).

Typer app exposing the full command surface. Phase 0–2 commands (auth, discover, scan
full/resume, runs) are implemented; later-phase commands (score/classify/select/supplier/
product/export) are registered as explicit "not yet implemented" stubs so the surface is
complete and discoverable.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv

from . import __version__
from .config import AppConfig, load_config
from .models import RunType
from .observability.errors import ScannerError
from .observability.logging import configure_logging, get_logger

app = typer.Typer(
    name="syncee-scanner",
    help="Syncee Home & Kitchen product research pipeline for RB Home.",
    no_args_is_help=True,
    add_completion=False,
)
auth_app = typer.Typer(help="Authentication (spec §7).", no_args_is_help=True)
scan_app = typer.Typer(help="Catalog scans (spec §17, §27, §28).", no_args_is_help=True)
score_app = typer.Typer(help="Scoring (spec §20, §24).", no_args_is_help=True)
select_app = typer.Typer(help="Selection (spec §26, §29).", no_args_is_help=True)
supplier_app = typer.Typer(help="Supplier overrides (spec §20.8).", no_args_is_help=True)
product_app = typer.Typer(help="Product overrides.", no_args_is_help=True)
runs_app = typer.Typer(help="Scan run inspection (spec §12).", no_args_is_help=True)
export_app = typer.Typer(help="CSV/JSON export (spec §38).", no_args_is_help=True)
baserow_app = typer.Typer(help="Baserow setup (spec §9).", no_args_is_help=True)
shopify_app = typer.Typer(
    help="Shopify Admin API push (publish-prep step 5).", no_args_is_help=True
)

app.add_typer(auth_app, name="auth")
app.add_typer(scan_app, name="scan")
app.add_typer(score_app, name="score")
app.add_typer(select_app, name="select")
app.add_typer(supplier_app, name="supplier")
app.add_typer(product_app, name="product")
app.add_typer(runs_app, name="runs")
app.add_typer(export_app, name="export")
app.add_typer(baserow_app, name="baserow")
app.add_typer(shopify_app, name="shopify")

log = get_logger("cli")


# --- Shared helpers ----------------------------------------------------------------


def _load(config: str | None, debug: bool) -> AppConfig:
    # Load .env (cwd/parents) into the environment so BASEROW_* etc. are picked up.
    # override=False keeps any real shell-exported vars authoritative.
    load_dotenv(override=False)
    configure_logging(debug=debug)
    try:
        return load_config(config)
    except ScannerError as exc:
        typer.secho(f"[{exc.code.value}] {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _fail(exc: ScannerError) -> None:
    typer.secho(f"[{exc.code.value}] {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _not_implemented(phase: str, what: str) -> None:
    typer.secho(
        f"'{what}' is planned for {phase} and is not yet implemented.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(3)


def _build_persistence(config: AppConfig, *, dry_run: bool):
    """Return (persistence, description). Postgres (DATABASE_URL) > Baserow > in-memory."""
    import os

    from .runs.persistence import InMemoryPersistence

    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        from .postgres.persistence import PostgresPersistence

        return PostgresPersistence(dsn, dry_run=dry_run), "Postgres"

    creds = config.baserow_credentials
    if creds.database_token and creds.suppliers_table_id:
        from .baserow.client import BaserowClient
        from .baserow.persistence import BaserowPersistence
        from .baserow.validation import validate_all

        client = BaserowClient(
            creds.api_url, creds.database_token,
            max_retries=config.baserow.max_retries,
            retry_backoff_seconds=config.baserow.retry_backoff_seconds,
        )
        table_ids = _resolve_table_ids(creds)
        validate_all(client, table_ids)  # spec §43.3: validate schema before scan
        return (
            BaserowPersistence(
                client, table_ids,
                create_batch_size=config.baserow.create_batch_size,
                update_batch_size=config.baserow.update_batch_size,
                dry_run=dry_run,
            ),
            "Baserow",
        )
    return InMemoryPersistence(), "in-memory (no Baserow configured)"


def _resolve_table_ids(creds) -> dict[str, str | int]:
    from .baserow.schemas import (
        T_MANUAL_DECISIONS,
        T_PRODUCT_CHANGES,
        T_PRODUCTS,
        T_SCAN_RUNS,
        T_SELECTION_BATCHES,
        T_SUPPLIERS,
    )

    return {
        T_SUPPLIERS: creds.suppliers_table_id,
        T_PRODUCTS: creds.products_table_id,
        T_SCAN_RUNS: creds.scan_runs_table_id,
        T_PRODUCT_CHANGES: creds.product_changes_table_id,
        T_MANUAL_DECISIONS: creds.manual_decisions_table_id,
        T_SELECTION_BATCHES: creds.selection_batches_table_id,
    }


def _print_summary(summary) -> None:
    typer.secho("Scan summary", fg=typer.colors.GREEN, bold=True)
    for k, v in summary.as_console_dict().items():
        typer.echo(f"  {k:>22}: {v}")


def _review_persistence(config: AppConfig, *, dry_run: bool = False):
    """Return a Baserow-backed persistence for scoring/selection/override commands.

    These operate on already-persisted rows, so an in-memory backend (empty across CLI
    invocations) is not usable — require Baserow.
    """
    persistence, backend = _build_persistence(config, dry_run=dry_run)
    if backend.startswith("in-memory"):
        typer.secho(
            "This command needs Baserow configured (set BASEROW_* env vars).",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(2)
    return persistence


def _activate_fx(config: AppConfig) -> None:
    """Load today's EUR exchange rates (cached daily) and make them active for margin."""
    from .pricing import fx

    rates = fx.load_rates(config.currency)
    fx.set_active(rates)
    typer.echo(
        f"FX rates: {rates.source} ({len(rates.to_eur)} currencies, base {config.currency.target})"
    )


def _print_kv(title: str, data: dict) -> None:
    typer.secho(title, fg=typer.colors.GREEN, bold=True)
    for k, v in data.items():
        typer.echo(f"  {k:>22}: {v}")


# --- auth --------------------------------------------------------------------------


@auth_app.command("login")
def auth_login(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Manual Syncee login (headed), saving the browser session (spec §7.1)."""
    cfg = _load(config, debug)
    from .browser import auth

    try:
        auth.login(cfg)
        typer.secho("Session saved.", fg=typer.colors.GREEN)
    except ScannerError as exc:
        _fail(exc)


@auth_app.command("validate")
def auth_validate(
    config: str | None = typer.Option(None, "--config"),
    headed: bool = typer.Option(False, "--headed"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Validate the saved session before scanning (spec §7.2)."""
    cfg = _load(config, debug)
    if headed:
        cfg.syncee.headless = False
    from .browser import auth

    try:
        auth.validate(cfg)
        typer.secho("Session is valid.", fg=typer.colors.GREEN)
    except ScannerError as exc:
        _fail(exc)


# --- discover ----------------------------------------------------------------------


@app.command("discover")
def discover(
    config: str | None = typer.Option(None, "--config"),
    output_dir: str = typer.Option("artifacts/discovery", "--output-dir"),
    url: str | None = typer.Option(
        None, "--url", help="Go straight to this listing URL instead of the marketplace home."
    ),
    headed: bool = typer.Option(False, "--headed"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Inspect Syncee structure and write discovery artifacts (spec §8)."""
    cfg = _load(config, debug)
    if headed:
        cfg.syncee.headless = False
    from .discovery.discover import run_discovery

    try:
        findings = run_discovery(cfg, output_dir=output_dir, target_url=url)
        typer.secho(
            f"Discovery written to {output_dir} "
            f"(gate ready: {'YES' if findings.gate_ready() else 'NO'})",
            fg=typer.colors.GREEN,
        )
    except ScannerError as exc:
        _fail(exc)


# --- scan --------------------------------------------------------------------------


@scan_app.command("full")
def scan_full(
    config: str | None = typer.Option(None, "--config"),
    category: str | None = typer.Option(None, "--category"),
    limit: int | None = typer.Option(None, "--limit", help="Cap products (smoke test)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="No status/row writes (spec §41.3)."),
    fixture: str | None = typer.Option(
        None, "--fixture", help="Scan a saved JSON fixture instead of live Syncee (offline)."
    ),
    debug: bool = typer.Option(False, "--debug"),
):
    """Full Home & Kitchen scan (spec §17). Use --fixture for an offline smoke test."""
    cfg = _load(config, debug)
    if category:
        cfg.syncee.category = category
    try:
        source = _make_source(cfg, fixture)
        persistence, backend = _build_persistence(cfg, dry_run=dry_run)
        typer.echo(f"Persistence: {backend}{' (dry-run)' if dry_run else ''}")
        from .scan import run_scan

        summary = run_scan(cfg, source=source, persistence=persistence,
                           run_type=RunType.FULL_SCAN, limit=limit)
        _print_summary(summary)
    except ScannerError as exc:
        _fail(exc)


@scan_app.command("resume")
def scan_resume(
    run_id: str = typer.Argument(...),
    config: str | None = typer.Option(None, "--config"),
    fixture: str | None = typer.Option(None, "--fixture", help="Offline JSON source."),
    debug: bool = typer.Option(False, "--debug"),
):
    """Resume an interrupted scan from its checkpoint (spec §17.5)."""
    cfg = _load(config, debug)
    from .scan import resume_scan

    try:
        persistence = _review_persistence(cfg)
        source = _make_source(cfg, fixture)
        summary = resume_scan(cfg, source=source, persistence=persistence, run_id=run_id)
        _print_summary(summary)
    except ScannerError as exc:
        _fail(exc)


@scan_app.command("incremental")
def scan_incremental(
    config: str | None = typer.Option(None, "--config"),
    fixture: str | None = typer.Option(None, "--fixture", help="Offline JSON source."),
    newest_first_verified: bool = typer.Option(
        False, "--newest-first-verified",
        help="Assert the source is verified newest-first (else completeness=Unverified).",
    ),
    no_score: bool = typer.Option(False, "--no-score", help="Skip the scoring chain."),
    debug: bool = typer.Option(False, "--debug"),
):
    """Weekly incremental scan + scoring chain (spec §27, §39)."""
    cfg = _load(config, debug)
    from .incremental import run_incremental_scan

    try:
        persistence = _review_persistence(cfg)
        source = _make_source(cfg, fixture)
        result = run_incremental_scan(
            cfg, source=source, persistence=persistence,
            newest_first_verified=newest_first_verified,
        )
        _print_summary(result.summary)
        typer.echo(f"  new products: {len(result.new_product_keys)} | "
                   f"changes recorded: {result.changes_recorded}")
        if not no_score:
            # Score new suppliers, exclude, score new/changed products (spec §39).
            from .scoring.service import score_products, score_suppliers

            _print_kv("Supplier scoring", score_suppliers(persistence, cfg).__dict__)
            _print_kv("Product scoring", score_products(persistence, cfg).__dict__)
    except ScannerError as exc:
        _fail(exc)


@scan_app.command("reconcile")
def scan_reconcile(
    config: str | None = typer.Option(None, "--config"),
    fixture: str | None = typer.Option(None, "--fixture", help="Offline JSON source."),
    debug: bool = typer.Option(False, "--debug"),
):
    """Reconciliation scan: refresh + mark missing products inactive (spec §28)."""
    cfg = _load(config, debug)
    from .reconcile import run_reconciliation_scan

    try:
        persistence = _review_persistence(cfg)
        source = _make_source(cfg, fixture)
        result = run_reconciliation_scan(cfg, source=source, persistence=persistence)
        _print_summary(result.summary)
        typer.echo(f"  products marked inactive: {result.inactive_marked}")
    except ScannerError as exc:
        _fail(exc)


def _make_source(cfg: AppConfig, fixture: str | None):
    from .extraction.source import FixtureSource, SynceeSource

    if fixture:
        return FixtureSource.from_file(Path(fixture))

    # Live: declarative mapper + cookie-authenticated API transport (spec §5.4, §8.4).
    from .browser.transport import SynceeApiTransport
    from .extraction.mapper import SynceeResponseMapper, load_mapping

    mapping = load_mapping()
    transport = SynceeApiTransport(cfg, mapping)  # raises CONFIGURATION_ERROR until endpoint set
    return SynceeSource(cfg, transport=transport, mapper=SynceeResponseMapper(mapping))


def _make_transport(cfg: AppConfig):
    from .browser.transport import SynceeApiTransport
    from .extraction.mapper import load_mapping

    return SynceeApiTransport(cfg, load_mapping())


@shopify_app.command("auth")
def shopify_auth(
    env_path: str = typer.Option(".env", "--env", help="Path to the .env file to update."),
    port: int = typer.Option(
        3456, "--port", help="Loopback callback port (match the app's redirect URL)."
    ),
) -> None:
    """One-time OAuth: mint a permanent Admin API access token and save it to .env."""
    from dotenv import load_dotenv

    from .publishing.shopify import ShopifyCreds, run_oauth_flow, upsert_env

    load_dotenv(env_path, override=False)
    try:
        creds = ShopifyCreds.from_env()
    except ScannerError as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from exc

    redirect = f"http://localhost:{port}/callback"
    typer.echo(f"Connecting {creds.shop} — a browser window will open for you to approve.")
    typer.echo(f"(Ensure the app's redirect URL includes: {redirect})")
    try:
        token = run_oauth_flow(creds, redirect_uri=redirect)
    except ScannerError as exc:
        typer.echo(f"Authorization failed: {exc}")
        raise typer.Exit(1) from exc

    upsert_env(env_path, "SHOPIFY_ADMIN_TOKEN", token)
    typer.echo(f"✓ Admin API access token saved to {env_path} (SHOPIFY_ADMIN_TOKEN).")


@shopify_app.command("prep")
def shopify_prep(
    config: str | None = typer.Option(None, "--config"),
    key: str | None = typer.Option(
        None, "--key", help="Prep a single Product Key (or row id) only."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Cap how many products to prep."),
    content: bool = typer.Option(True, "--content/--no-content", help="Generate SEO copy."),
    images: bool = typer.Option(True, "--images/--no-images", help="Generate the product image."),
    force: bool = typer.Option(False, "--force", help="Re-run even if already prepped."),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Publish-prep the assortment: normalize + SEO copy + generative image (via OpenRouter)."""
    cfg = _load(config, debug)
    persistence = _review_persistence(cfg)
    from .publishing.openrouter import OpenRouterClient
    from .publishing.service import run_publish_prep

    try:
        transport = OpenRouterClient.from_env(cfg.publishing.seo.base_url)
    except ScannerError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    results = run_publish_prep(
        persistence, cfg, transport,
        keys=[key] if key else None, limit=limit,
        do_content=content, do_images=images, force=force,
    )
    ready = [r for r in results if r["status"] == "Ready to Publish"]
    typer.echo(f"Prepped {len(results)} product(s) — {len(ready)} ready to publish")
    for r in results:
        typer.echo(f"  • {(r.get('name') or '')[:40]:40} → {r['status']}")


@shopify_app.command("push")
def shopify_push(
    config: str | None = typer.Option(None, "--config"),
    apply: bool = typer.Option(False, "--apply", help="Write to Shopify (default: dry-run)."),
    key: str | None = typer.Option(None, "--key", help="Push a single Product Key only."),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Match assortment products to their Shopify listings and push copy/SEO/image."""
    cfg = _load(config, debug)
    persistence = _review_persistence(cfg)
    from .publishing.shopify import ShopifyClient, ShopifyCreds, push_products

    client = ShopifyClient(ShopifyCreds.from_env(), api_version=cfg.publishing.shopify.api_version)
    try:
        results = push_products(
            persistence, client, cfg, dry_run=not apply, keys=[key] if key else None
        )
    finally:
        client.close()

    pushed = [r for r in results if r["status"] in ("would-push", "pushed")]
    mode = "APPLIED" if apply else "DRY-RUN"
    typer.echo(f"{mode} — {len(pushed)} of {len(results)} matched a Shopify product")
    for r in pushed:
        dup = f"  ⚠ {r['matches']} duplicate listings" if r["matches"] > 1 else ""
        tags = ("SEO+" if r["seo"] else "") + ("image+" if r["image"] else "")
        title = (r["new_title"] or "")[:40]
        typer.echo(f"  • {(r['name'] or '')[:36]:36} → «{title}» {tags}{dup}")
    missing = [r for r in results if r["status"] == "not-imported"]
    if missing:
        typer.echo(f"  ({len(missing)} not yet imported into Shopify via Syncee)")


@app.command("enrich")
def enrich(
    config: str | None = typer.Option(None, "--config"),
    limit: int | None = typer.Option(
        None, "--limit", help="Enrich at most N products this run (a chunk). Repeat to continue."
    ),
    top: int | None = typer.Option(
        None, "--top", help="Enrich only the top-N pre-scored products (default: all)."
    ),
    collection: str | None = typer.Option(
        None, "--collection", help="Only enrich products in this collection."
    ),
    shortlisted_only: bool = typer.Option(
        False, "--shortlisted-only", help="Only enrich currently-shortlisted products."
    ),
    reenrich: bool = typer.Option(
        False, "--reenrich", help="Re-enrich products already enriched (default: skip them)."
    ),
    debug: bool = typer.Option(False, "--debug"),
):
    """Fetch product detail (real shipping/description/stock) — all products by default.

    Enriches every not-yet-enriched product; use ``--limit`` to enrich in chunks (each run
    skips what's already done, so just repeat until it reports 0). ``Enriched At`` marks a
    product done (spec §5.4).
    """
    cfg = _load(config, debug)
    from .enrich import enrich_products
    from .extraction.mapper import SynceeResponseMapper, load_mapping

    try:
        persistence = _review_persistence(cfg)
        transport = _make_transport(cfg)
        statuses = {"Shortlisted"} if shortlisted_only else None
        try:
            result = enrich_products(
                persistence, transport, cfg, SynceeResponseMapper(load_mapping()),
                top=top, review_status=statuses, collection=collection,
                limit=limit, skip_enriched=not reenrich,
            )
        finally:
            transport.close()
        _print_kv("Enrichment", {"enriched": result.enriched, "failed": result.failed,
                                 "suppliers_updated": result.suppliers_updated})
    except ScannerError as exc:
        _fail(exc)


@app.command("pipeline")
def pipeline(
    what: str = typer.Argument("initial", help="Currently only 'initial'."),
    config: str | None = typer.Option(None, "--config"),
    limit: int | None = typer.Option(None, "--limit", help="Overall scan product cap."),
    enrich_top: int = typer.Option(120, "--enrich-top", help="How many finalists to enrich."),
    enrich_per_supplier: int = typer.Option(
        4, "--enrich-per-supplier", help="Max finalists per supplier (spreads across suppliers)."
    ),
    debug: bool = typer.Option(False, "--debug"),
):
    """Run the full initial-assortment funnel: scan → prerank → enrich → rescore → select."""
    if what != "initial":
        _not_implemented("later", f"pipeline {what}")
    cfg = _load(config, debug)
    _activate_fx(cfg)  # EUR conversion for the rescore step
    from .pipeline import run_initial_pipeline

    try:
        persistence = _review_persistence(cfg)
        result = run_initial_pipeline(
            cfg, persistence,
            make_source=lambda: _make_source(cfg, None),
            make_transport=lambda: _make_transport(cfg),
            scan_limit=limit, enrich_top=enrich_top,
            enrich_per_supplier=enrich_per_supplier,
        )
    except ScannerError as exc:
        _fail(exc)
        return
    typer.secho("Pipeline complete", fg=typer.colors.GREEN, bold=True)
    _print_kv("Funnel", {
        "scanned": result.scan_products,
        "shortlisted (prerank)": result.prerank_shortlisted,
        "enriched": result.enrich.enriched,
        "shortlisted (final)": result.final_shortlisted,
    })
    _print_selection(result.batch)


# --- score / classify / select (later phases) --------------------------------------


@score_app.command("suppliers")
def cmd_score_suppliers(
    config: str | None = typer.Option(None, "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Supplier hard gates + weighted scoring (spec §20)."""
    cfg = _load(config, debug)
    from .scoring.service import score_suppliers

    try:
        summary = score_suppliers(_review_persistence(cfg, dry_run=dry_run), cfg)
        _print_kv("Supplier scoring", summary.__dict__)
    except ScannerError as exc:
        _fail(exc)


@score_app.command("products")
def cmd_score_products(
    config: str | None = typer.Option(None, "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    pricing_mode: str | None = typer.Option(
        None, "--pricing-mode", help="Override: rrp | target_margin | markup."
    ),
    target_margin: float | None = typer.Option(
        None, "--target-margin", help="Override target gross margin %% (target_margin mode)."
    ),
    min_margin: float | None = typer.Option(
        None, "--min-margin", help="Override minimum margin %% to keep a product."
    ),
    debug: bool = typer.Option(False, "--debug"),
):
    """Product hard gates + margin + weighted scoring + classification (spec §22-§25).

    The pricing overrides let you re-price/re-score without editing config + rebuilding.
    """
    cfg = _load(config, debug)
    if pricing_mode:
        if pricing_mode not in ("rrp", "target_margin", "markup"):
            typer.secho(f"Invalid --pricing-mode '{pricing_mode}'.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        cfg.margin.pricing_mode = pricing_mode
    if target_margin is not None:
        cfg.margin.target_margin_pct = target_margin
    if min_margin is not None:
        cfg.margin.minimum_margin_pct = min_margin
    typer.echo(
        f"Pricing: {cfg.margin.pricing_mode} | target {cfg.margin.target_margin_pct}% | "
        f"min {cfg.margin.minimum_margin_pct}%"
    )
    _activate_fx(cfg)  # convert supplier prices to EUR with today's rates before margin
    from .scoring.service import score_products

    try:
        summary = score_products(_review_persistence(cfg, dry_run=dry_run), cfg)
        _print_kv("Product scoring", summary.__dict__)
    except ScannerError as exc:
        _fail(exc)


@app.command("classify")
def classify(
    what: str = typer.Argument("products"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Collection classification is applied during `score products` (spec §25)."""
    typer.secho(
        "Classification runs as part of `score products`. Run that command.",
        fg=typer.colors.YELLOW,
    )


@select_app.command("initial")
def cmd_select_initial(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Initial 18–24 product assortment candidate batch (spec §26)."""
    cfg = _load(config, debug)
    from .selection.service import make_initial_assortment

    try:
        out = make_initial_assortment(_review_persistence(cfg), cfg)
        _print_selection(out)
    except ScannerError as exc:
        _fail(exc)


@select_app.command("new-arrivals")
def cmd_select_new_arrivals(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """4-product new-arrivals candidate batch (spec §29)."""
    cfg = _load(config, debug)
    from .selection.service import make_new_arrivals

    try:
        out = make_new_arrivals(_review_persistence(cfg), cfg)
        _print_selection(out)
    except ScannerError as exc:
        _fail(exc)


def _print_selection(out: dict) -> None:
    result = out["result"]
    typer.secho(f"Batch {out['batch_id']} created ({result.count} candidates)",
                fg=typer.colors.GREEN, bold=True)
    for col, n in result.per_collection.items():
        typer.echo(f"  {col.value:>22}: {n}")
    for note in result.notes:
        typer.secho(f"  note: {note}", fg=typer.colors.YELLOW)


def _supplier_override(config, supplier_key, override, note, debug):
    cfg = _load(config, debug)
    from .scoring.overrides import apply_supplier_override

    try:
        new_status = apply_supplier_override(
            _review_persistence(cfg), cfg, supplier_key, override, note=note
        )
        typer.secho(f"{supplier_key} -> {new_status}", fg=typer.colors.GREEN)
    except ScannerError as exc:
        _fail(exc)


@supplier_app.command("approve")
def supplier_approve(
    supplier_key: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Manually approve a supplier (audited, overrides gates — spec §20.8)."""
    from .models import ManualOverride

    _supplier_override(config, supplier_key, ManualOverride.APPROVE, note, debug)


@supplier_app.command("block")
def supplier_block(
    supplier_key: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Manually block a supplier (audited — spec §20.8)."""
    from .models import ManualOverride

    _supplier_override(config, supplier_key, ManualOverride.BLOCK, note, debug)


@supplier_app.command("clear-override")
def supplier_clear(
    supplier_key: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Clear a supplier's manual override and rescore (spec §20.8)."""
    from .models import ManualOverride

    _supplier_override(config, supplier_key, ManualOverride.NONE, note, debug)


def _product_decision(config, product_key, decision, note, debug):
    cfg = _load(config, debug)
    from .scoring.overrides import apply_product_decision

    try:
        new_status = apply_product_decision(
            _review_persistence(cfg), product_key, decision, note=note
        )
        typer.secho(f"{product_key} -> {new_status}", fg=typer.colors.GREEN)
    except ScannerError as exc:
        _fail(exc)


@product_app.command("approve")
def product_approve(
    product_key: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Manually approve a product (audited — spec §14)."""
    from .models import DecisionValue

    _product_decision(config, product_key, DecisionValue.APPROVE, note, debug)


@product_app.command("reject")
def product_reject(
    product_key: str = typer.Argument(...),
    note: str | None = typer.Option(None, "--note"),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Manually reject a product (audited — spec §14)."""
    from .models import DecisionValue

    _product_decision(config, product_key, DecisionValue.REJECT, note, debug)


# --- runs --------------------------------------------------------------------------


@runs_app.command("list")
def runs_list(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """List recent scan runs (spec §12)."""
    cfg = _load(config, debug)
    for row in _iter_runs(cfg):
        typer.echo(
            f"{row.get('Run ID',''):40} {str(row.get('Run Type','')):18} "
            f"{str(row.get('Status','')):22} seen={row.get('Products Seen','')}"
        )


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(...),
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Show one scan run's detail (spec §12)."""
    cfg = _load(config, debug)
    for row in _iter_runs(cfg):
        if row.get("Run ID") == run_id:
            typer.echo(json.dumps(row, indent=2, default=str))
            return
    typer.secho(f"Run '{run_id}' not found.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _iter_runs(cfg: AppConfig):
    creds = cfg.baserow_credentials
    if not (creds.database_token and creds.scan_runs_table_id):
        typer.secho("No Baserow scan-runs table configured.", fg=typer.colors.YELLOW, err=True)
        return []
    from .baserow.client import BaserowClient

    client = BaserowClient(creds.api_url, creds.database_token)
    return list(client.iter_rows(creds.scan_runs_table_id))


# --- baserow setup -----------------------------------------------------------------


def _jwt_credentials(creds, *, need_workspace: bool):
    """Resolve email/password/workspace for JWT-only schema operations.

    Runtime API access uses the database token; only schema creation needs a JWT (Baserow
    tokens cannot create tables/views). The password is prompted (hidden) when not in the
    environment, so it never has to be persisted in .env.
    """
    email = creds.user_email or typer.prompt("Baserow user email")
    password = creds.user_password or typer.prompt("Baserow password", hide_input=True)
    workspace = creds.workspace_id
    if need_workspace and not workspace:
        workspace = typer.prompt("Baserow workspace ID")
    return email, password, workspace


@baserow_app.command("guide")
def baserow_guide(
    output: str | None = typer.Option(None, "--output", help="Write the guide to a file."),
):
    """Print a manual setup guide — create the schema by hand, no password needed (spec §9)."""
    from .baserow.guide import render_setup_guide

    text = render_setup_guide()
    if output:
        Path(output).write_text(text, encoding="utf-8")
        typer.secho(f"Guide written to {output}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


@baserow_app.command("setup")
def baserow_setup(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Auto-create the database + tables + views via a user JWT (spec §9).

    Only needed to create the schema. Prefer `baserow guide` if you'd rather create tables
    manually and run with just a database token.
    """
    cfg = _load(config, debug)
    creds = cfg.baserow_credentials
    from .baserow.schemas import TABLE_ENV_VARS
    from .baserow.setup import run_setup

    email, password, workspace = _jwt_credentials(creds, need_workspace=True)
    try:
        table_ids = run_setup(
            api_url=creds.api_url, email=email, password=password, workspace_id=workspace,
        )
    except ScannerError as exc:
        _fail(exc)
        return
    typer.secho("Tables + views created. Add these to your .env:", fg=typer.colors.GREEN, bold=True)
    for name, env_var in TABLE_ENV_VARS.items():
        typer.echo(f"{env_var}={table_ids[name]}")


@baserow_app.command("setup-fields")
def baserow_setup_fields(
    config: str | None = typer.Option(None, "--config"),
    no_views: bool = typer.Option(False, "--no-views", help="Skip creating views."),
    debug: bool = typer.Option(False, "--debug"),
):
    """Add the schema's fields to tables you already created (uses the IDs in .env).

    Idempotent: renames the primary field, creates only missing fields, deletes nothing.
    Baserow tokens can't create fields, so this authenticates via a user JWT (password
    prompted if not in the environment).
    """
    cfg = _load(config, debug)
    creds = cfg.baserow_credentials
    try:
        creds.require_tables()  # needs the token + all six table IDs
    except ScannerError as exc:
        _fail(exc)
        return
    from .baserow.setup import run_setup_fields

    email, password, _ = _jwt_credentials(creds, need_workspace=False)
    try:
        summary = run_setup_fields(
            api_url=creds.api_url, email=email, password=password,
            table_ids=_resolve_table_ids(creds), views=not no_views,
        )
    except ScannerError as exc:
        _fail(exc)
        return
    typer.secho("Fields synced:", fg=typer.colors.GREEN, bold=True)
    for table, counts in summary.items():
        typer.echo(
            f"  {table:>18}: +{counts['created']} created, {counts['skipped']} already present"
        )


@baserow_app.command("views")
def baserow_views(
    config: str | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Create the operational review views on existing tables (spec §30)."""
    cfg = _load(config, debug)
    creds = cfg.baserow_credentials
    try:
        creds.require_tables()
    except ScannerError as exc:
        _fail(exc)
        return
    from .baserow.setup import run_views

    email, password, _ = _jwt_credentials(creds, need_workspace=False)
    try:
        count = run_views(
            api_url=creds.api_url, email=email, password=password,
            table_ids=_resolve_table_ids(creds),
        )
        typer.secho(f"Created {count} views.", fg=typer.colors.GREEN)
    except ScannerError as exc:
        _fail(exc)


# --- export (spec §38) -------------------------------------------------------------


def _run_export(config, debug, output_dir, fn_name, **kw):
    cfg = _load(config, debug)
    from . import export as _  # noqa: F401
    from .export import service

    try:
        persistence = _review_persistence(cfg)
        paths = getattr(service, fn_name)(persistence, output_dir, **kw)
        typer.secho("Exported:", fg=typer.colors.GREEN, bold=True)
        for p in paths:
            typer.echo(f"  {p}")
    except ScannerError as exc:
        _fail(exc)


@export_app.command("suppliers")
def export_suppliers(
    config: str | None = typer.Option(None, "--config"),
    output_dir: str = typer.Option("exports", "--output-dir"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Export suppliers to CSV (spec §38)."""
    _run_export(config, debug, output_dir, "export_suppliers")


@export_app.command("products")
def export_products(
    config: str | None = typer.Option(None, "--config"),
    output_dir: str = typer.Option("exports", "--output-dir"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Export products to CSV (spec §38)."""
    _run_export(config, debug, output_dir, "export_products")


@export_app.command("candidates")
def export_candidates(
    config: str | None = typer.Option(None, "--config"),
    output_dir: str = typer.Option("exports", "--output-dir"),
    debug: bool = typer.Option(False, "--debug"),
):
    """Export selection candidates to CSV (spec §38)."""
    _run_export(config, debug, output_dir, "export_candidates")


@export_app.command("all")
def export_all(
    config: str | None = typer.Option(None, "--config"),
    output_dir: str = typer.Option("exports", "--output-dir"),
    json_too: bool = typer.Option(False, "--json", help="Also write JSON exports."),
    debug: bool = typer.Option(False, "--debug"),
):
    """Export suppliers + products + candidates (spec §38)."""
    _run_export(config, debug, output_dir, "export_all", json=json_too)


# --- version -----------------------------------------------------------------------


@app.command("version")
def version():
    """Print the scanner version."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
