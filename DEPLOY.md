# Deploying to your VPS

Self-contained Docker stack: **Postgres** (the store) + a **FastAPI review dashboard**,
plus the **scanner CLI** as a one-shot runner. No Baserow, no external image host — processed
images live in Postgres and are served by the dashboard.

## What runs

| Service     | Role                                            | Exposed            |
|-------------|-------------------------------------------------|--------------------|
| `db`        | Postgres 16, all pipeline state (JSONB)         | internal only      |
| `dashboard` | Review/approve UI (`uvicorn`)                   | `:8000` (host)     |
| `scanner`   | The `syncee-scanner` CLI, run on demand         | — (`compose run`)  |

## First-time setup

```bash
# 1. Configure
cp .env.example .env
#    → set POSTGRES_PASSWORD, OPENROUTER_API_KEY, the SHOPIFY_* vars, and
#      DASHBOARD_PASSWORD (see "Login" below).

# 2. Build + start Postgres and the dashboard
docker compose up -d --build

# 3. Confirm it's healthy
curl -s localhost:8000/health        # {"status":"ok","products":0}
```

The dashboard is now on `http://<vps-ip>:8000`.

## Login

The dashboard has its own sign-in — no reverse-proxy basic-auth needed. Set in `.env`:

```bash
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=<a strong password>     # enables login; blank = open
DASHBOARD_COOKIE_SECURE=1                   # set once you're serving over HTTPS
# DASHBOARD_SECRET=<openssl rand -hex 32>   # optional; defaults to a key derived from the password
```

With `DASHBOARD_PASSWORD` set, every page requires a signed session cookie (14-day expiry,
HttpOnly); `/health` stays public for monitoring. Leaving it **blank** keeps the dashboard open
— only do that if it already sits behind a trusted proxy. Changing the password invalidates all
existing sessions. Still terminate TLS with a reverse proxy (Caddy/nginx) for HTTPS; the login
replaces the need for proxy-level auth.

## Providing the Syncee session

Live scans need a logged-in Syncee session. The browser login is interactive, so create the
session **on your laptop** and copy it up:

```bash
# On your laptop (headed browser):
syncee-scanner auth login          # writes data/auth/storage_state.json

# Copy it to the VPS (the scanner service mounts ./data):
scp -r data/auth <vps>:/path/to/syncee-product-research/data/
```

`./data` is a bind mount, so the session persists across container restarts. Re-run
`auth login` + copy whenever it expires.

## Running the pipeline

The `scanner` service shares the image, DB, and env. Pass any CLI command after `run`.
The flow is **scan → enrich → score → review → publish** — download everything, enrich
everything, and only then score (no top-N cap starving categories). Only image **URLs** are
stored; images are never downloaded during scan/enrich.

```bash
# 1. Download the catalogue (resumable; run per category to go chunk by chunk):
docker compose run --rm scanner scan full
#    or, one slice at a time:
docker compose run --rm scanner scan full --category "Home & Kitchen"

# 2. Enrich ALL products — real shipping/description/stock. Chunk with --limit and just
#    repeat until it reports 0 (each run skips what's already enriched):
docker compose run --rm scanner enrich --limit 500
docker compose run --rm scanner enrich --limit 500
#    ... until "enriched: 0"

# 3. Score suppliers then products (classification happens inside score products):
docker compose run --rm scanner score suppliers
docker compose run --rm scanner score products

# 4. Review/approve in the dashboard, then build the assortment:
docker compose run --rm scanner select initial

# 5. Publish-prep (SEO copy + generative images via OpenRouter) and push to Shopify:
docker compose run --rm scanner shopify prep
docker compose run --rm scanner shopify auth      # one-time OAuth
docker compose run --rm scanner shopify push --apply
```

Review and approve in the dashboard between steps — cards show the supplier image URL directly
(no download), and approvals/pins write straight back to the same Postgres rows the CLI reads.

> Run `docker compose run --rm scanner --help` (or `<group> --help`) to see every command
> and its options.

### Or drive it from the dashboard

The **Control** tab (`/control`) runs scan and enrich for you — no shell needed:

- **Download (scan)** — optional category, starts a headless scan into Postgres.
- **Enrich** — optional chunk size / collection / re-enrich; skips already-done products.
- **Status** — live catalogue totals (products / suppliers / enriched / scored), an
  enrichment progress bar, per-collection and review-status breakdowns, and the latest scan-run
  summary.
- **Jobs** run one at a time in the background; each has a live-updating log page, and the list
  shows recent runs with success/failure.
- **Pause / Resume / Cancel** any running job (from the active banner, the jobs table, or the job
  page). Pause freezes the process (SIGSTOP) and resume continues it (SIGCONT); cancel stops it
  (SIGTERM), and a second cancel force-kills (SIGKILL). Crashed/killed jobs are auto-reaped so a
  dead job never blocks the single job slot.

The **Products** tab adds filtering (collection / review / selection / enriched / search),
sorting (score / price / name / recently-enriched), and grouping (collection / supplier /
review / selection) with headers. It also supports:

- **Full product detail** — click any card's image or title to open everything stored for that
  product: image gallery, publish copy (title / SEO / meta / handle / tags / description),
  pricing & margin, shipping & stock, scoring & reason codes, the supplier, the **variants**
  table, and a collapsible dump of every field + the raw Syncee record.
- **Bulk approve / reject / shortlist** — tick the checkbox on each card (or "Select all") and
  use the top bar to action them all at once.

These controls and the `scanner` CLI act on the same Postgres, so mix and match freely.

## Data & backups

Everything is in the `pgdata` volume. Back it up with:

```bash
docker compose exec db pg_dump -U postgres rbhome | gzip > rbhome-$(date +%F).sql.gz
```

Restore into a fresh stack with `gunzip -c rbhome-*.sql.gz | docker compose exec -T db psql -U postgres rbhome`.

## Updating

```bash
git pull
docker compose up -d --build        # rebuilds, keeps the pgdata volume
```

## Notes

- **Secrets** only ever live in `.env` (git-ignored) and are injected as env vars — never baked
  into the image.
- The scanner image bundles Chromium (Playwright base image), so headless scans work on the VPS.
- Postgres is not published to the host by default; uncomment the `ports:` line under `db` in
  `docker-compose.yml` only if you need to connect a SQL client directly.
