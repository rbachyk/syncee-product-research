"""Background job runner for the dashboard.

Launches the ``syncee-scanner`` CLI (scan / enrich) as a subprocess and tracks it in the
``jobs`` Postgres table, so status + a tail of the log survive across web requests and restarts.
Only one job runs at a time (both scan and enrich hit live Syncee and are heavy). Reusing the
CLI keeps all the pipeline logic in one place — the dashboard just orchestrates.

Control is by OS signal to the tracked pid: pause = SIGSTOP, resume = SIGCONT, cancel = SIGTERM
(a second cancel escalates to SIGKILL). ``reap_dead()`` finalizes jobs whose process has
vanished so a crashed job never blocks the single-job slot.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

import psycopg
from psycopg.types.json import Jsonb

_LOCK = threading.Lock()
_LOG_TAIL = 12000  # chars of trailing log kept per job
_FLUSH_EVERY = 4   # update the DB log every N lines (plus on exit)
_ACTIVE = ("running", "paused", "cancelling")  # statuses that still hold the slot
_ORPHAN_GRACE_S = 60  # a pid-less 'running' job older than this is a failed launch


def _conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _alive(pid: int | None) -> bool:
    """True if a process with this pid exists (SIGSTOP-paused processes count as alive)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal(pid: int | None, sig: int) -> None:
    if not pid:
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _fetch(job_id: int) -> tuple[int | None, str] | None:
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT pid, status FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


# --- reads -------------------------------------------------------------------------

def active_job() -> dict | None:
    """The job still holding the slot (running / paused / cancelling), if any."""
    reap_dead()  # never let a dead job keep the slot
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, params, started_at FROM jobs "
            "WHERE status IN ('running', 'paused', 'cancelling') ORDER BY id DESC LIMIT 1"
        )
        r = cur.fetchone()
    cols = ["id", "kind", "status", "params", "started_at"]
    return dict(zip(cols, r, strict=True)) if r else None


def recent_jobs(limit: int = 12) -> list[dict]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, params, started_at, finished_at "
            "FROM jobs ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        cols = ["id", "kind", "status", "params", "started_at", "finished_at"]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def get_job(job_id: int) -> dict | None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, params, log, pid, started_at, finished_at "
            "FROM jobs WHERE id = %s",
            (job_id,),
        )
        r = cur.fetchone()
    if not r:
        return None
    cols = ["id", "kind", "status", "params", "log", "pid", "started_at", "finished_at"]
    return dict(zip(cols, r, strict=True))


# --- lifecycle ---------------------------------------------------------------------

def reconcile_stale() -> None:
    """On startup every subprocess is dead — finalize anything left mid-flight."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET "
            "status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'failed' END, "
            "finished_at = now(), log = log || E'\n[interrupted: dashboard restarted]' "
            "WHERE status IN ('running', 'paused', 'cancelling')"
        )


def reap_dead() -> None:
    """Finalize active jobs whose process has vanished (crash, external kill, dead thread)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, pid, status, extract(epoch FROM (now() - started_at)) "
            "FROM jobs WHERE status IN ('running', 'paused', 'cancelling')"
        )
        for jid, pid, status, age in cur.fetchall():
            dead = (pid is not None and not _alive(pid)) or (
                pid is None and (age or 0) > _ORPHAN_GRACE_S
            )
            if not dead:
                continue
            final = "cancelled" if status == "cancelling" else "failed"
            cur.execute(
                "UPDATE jobs SET status = %s, finished_at = now(), "
                "log = log || E'\n[reaped: process no longer running]' "
                "WHERE id = %s AND status IN ('running', 'paused', 'cancelling')",
                (final, jid),
            )


def start_job(kind: str, argv: list[str], params: dict) -> tuple[int | None, str | None]:
    """Insert a job row and spawn the subprocess. Returns (job_id, error)."""
    with _LOCK:
        if active_job():
            return None, "A job is already running — pause or cancel it first."
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (kind, params) VALUES (%s, %s) RETURNING id",
                (kind, Jsonb(params)),
            )
            job_id = cur.fetchone()[0]
    threading.Thread(target=_run, args=(job_id, argv), daemon=True).start()
    return job_id, None


def pause_job(job_id: int) -> str | None:
    row = _fetch(job_id)
    if not row:
        return "No such job."
    pid, status = row
    if status != "running":
        return f"Can't pause a {status} job."
    if not _alive(pid):
        reap_dead()
        return "Process is no longer running."
    _signal(pid, signal.SIGSTOP)
    with _conn() as c, c.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'paused' WHERE id = %s AND status = 'running'",
                    (job_id,))
    return None


def resume_job(job_id: int) -> str | None:
    row = _fetch(job_id)
    if not row:
        return "No such job."
    pid, status = row
    if status != "paused":
        return f"Can't resume a {status} job."
    if not _alive(pid):
        reap_dead()
        return "Process is no longer running."
    _signal(pid, signal.SIGCONT)
    with _conn() as c, c.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'running' WHERE id = %s AND status = 'paused'",
                    (job_id,))
    return None


def cancel_job(job_id: int) -> str | None:
    row = _fetch(job_id)
    if not row:
        return "No such job."
    pid, status = row
    if status not in _ACTIVE:
        return f"Job already {status}."
    if not _alive(pid):
        # Nothing to signal — mark it cancelled directly and let reap clean any orphan.
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = now() "
                "WHERE id = %s AND status IN ('running', 'paused', 'cancelling')",
                (job_id,),
            )
        return None
    if status == "cancelling":
        _signal(pid, signal.SIGKILL)  # second cancel → force kill
        return None
    # Mark intent so the runner finalizes to 'cancelled', then terminate (resume first so a
    # paused process can handle the signal).
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'cancelling' "
            "WHERE id = %s AND status IN ('running', 'paused')",
            (job_id,),
        )
    _signal(pid, signal.SIGCONT)
    _signal(pid, signal.SIGTERM)
    return None


def _run(job_id: int, argv: list[str]) -> None:
    conn = _conn()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is built from a fixed allow-list below
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=dict(os.environ),
        )
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET pid = %s WHERE id = %s", (proc.pid, job_id))
        buf: list[str] = []
        for i, line in enumerate(proc.stdout or []):
            buf.append(line)
            if (i + 1) % _FLUSH_EVERY == 0:
                _write_log(conn, job_id, buf)
        proc.wait()
        _write_log(conn, job_id, buf)
        # A concurrent cancel sets 'cancelling'; honour it. Otherwise use the exit code.
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
            cur_status = (cur.fetchone() or [None])[0]
        if cur_status == "cancelling":
            final = "cancelled"
        else:
            final = "succeeded" if proc.returncode == 0 else "failed"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, finished_at = now() "
                "WHERE id = %s AND status IN ('running', 'paused', 'cancelling')",
                (final, job_id),
            )
    except Exception as exc:  # noqa: BLE001 - record any launch/runtime failure in the job row
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'failed', finished_at = now(), "
                "log = log || %s WHERE id = %s AND status IN ('running', 'paused', 'cancelling')",
                (f"\n[runner error] {exc}", job_id),
            )
    finally:
        conn.close()


def _write_log(conn: psycopg.Connection, job_id: int, buf: list[str]) -> None:
    tail = "".join(buf)[-_LOG_TAIL:]
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET log = %s WHERE id = %s", (tail, job_id))


# --- argv builders (the only commands the dashboard may launch) --------------------

def scan_argv(category: str | None, source: str | None = None) -> list[str]:
    argv = ["syncee-scanner", "scan", "full"]
    if category:
        argv += ["--category", category]
    if source:
        argv += ["--source", source]
    return argv


def enrich_argv(
    limit: int | None, *, reenrich: bool = False, collection: str | None = None,
    source: str | None = None,
) -> list[str]:
    argv = ["syncee-scanner", "enrich"]
    if limit:
        argv += ["--limit", str(limit)]
    if collection:
        argv += ["--collection", collection]
    if reenrich:
        argv += ["--reenrich"]
    if source:
        argv += ["--source", source]
    return argv


def score_argv(
    target: str, *, pricing_mode: str | None = None, target_margin: float | None = None,
    markup: float | None = None, min_margin: float | None = None,
) -> list[str] | None:
    """`score suppliers` or `score products` (+ optional pricing overrides for re-scoring)."""
    if target not in ("suppliers", "products"):
        return None
    argv = ["syncee-scanner", "score", target]
    if target == "products":
        if pricing_mode:
            argv += ["--pricing-mode", pricing_mode]
        if target_margin is not None:
            argv += ["--target-margin", str(target_margin)]
        if markup is not None:
            argv += ["--markup", str(markup)]
        if min_margin is not None:
            argv += ["--min-margin", str(min_margin)]
    return argv


def select_argv(target: str) -> list[str] | None:
    """`select initial` (18–24 assortment batch) or `select new-arrivals` (4-product batch)."""
    if target not in ("initial", "new-arrivals"):
        return None
    return ["syncee-scanner", "select", target]


def prep_argv(limit: int | None = None) -> list[str]:
    """`shopify prep` — publish-prep (normalize + SEO copy + generative image)."""
    argv = ["syncee-scanner", "shopify", "prep"]
    if limit:
        argv += ["--limit", str(limit)]
    return argv


def push_argv(*, apply: bool = False, key: str | None = None) -> list[str]:
    """`shopify push` — dry-run by default; --apply writes to Shopify."""
    argv = ["syncee-scanner", "shopify", "push"]
    if apply:
        argv += ["--apply"]
    if key:
        argv += ["--key", key]
    return argv


def validate_argv() -> list[str]:
    """`auth validate` — check the saved Syncee session is still usable."""
    return ["syncee-scanner", "auth", "validate"]
