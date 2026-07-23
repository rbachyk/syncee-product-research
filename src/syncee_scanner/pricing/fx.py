"""EUR foreign-exchange conversion for pricing.

Syncee reports each product's wholesale/RRP/shipping in the *supplier's* currency; RB Home
sells in EUR. Every monetary value must therefore be converted to EUR before margin/retail is
computed — otherwise a price of, say, 4 000 HUF gets treated as €4 000.

Rates are ECB daily reference rates (via ``frankfurter.app`` by default — free, no API key),
cached to a file so a normal run just reads the cache and only refetches once the cache is
older than ``max_age_hours``. On any fetch failure we fall back to the last cache, then to the
static ``fallback_rates`` in config, so scans never break on a network blip.

Loading is done once by the CLI (``fx.set_active(fx.load_rates(cfg.currency))``); the margin
code reads the process-global via :func:`active`, so nothing needs the rates threaded through it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class FxRates:
    """EUR conversion table. ``to_eur[CCY]`` = the EUR value of 1 unit of CCY."""

    target: str = "EUR"
    to_eur: dict[str, float] = field(default_factory=dict)
    fetched_at: float | None = None
    source: str = "fallback"  # "live" | "cache" | "fallback"

    def convert(self, amount: float | None, currency: str | None) -> float | None:
        """Convert ``amount`` (in ``currency``) to EUR. Returns None for an unknown currency."""
        if amount is None:
            return None
        ccy = (currency or self.target).upper()
        if ccy == self.target:
            return amount
        rate = self.to_eur.get(ccy)
        return amount * rate if rate else None


def _fetch_frankfurter(url: str) -> dict[str, float]:
    """Fetch EUR-base rates and invert to EUR-per-unit. Expects ``{"rates": {"USD": 1.08}}``."""
    resp = httpx.get(url, timeout=15.0)
    resp.raise_for_status()
    per_eur = resp.json().get("rates", {})
    to_eur = {ccy: 1.0 / rate for ccy, rate in per_eur.items() if rate}
    to_eur["EUR"] = 1.0
    return to_eur


def _read_cache(path: Path) -> FxRates | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return FxRates(
        target=raw.get("target", "EUR"), to_eur=raw.get("to_eur", {}),
        fetched_at=raw.get("fetched_at"), source="cache",
    )


def _write_cache(path: Path, fx: FxRates) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "target": fx.target, "to_eur": fx.to_eur, "fetched_at": fx.fetched_at,
        }), encoding="utf-8")
    except OSError:
        pass  # a read-only cache dir must not break a scan


def load_rates(cfg, *, fetcher=_fetch_frankfurter, now: float | None = None) -> FxRates:
    """Load conversion rates: fresh cache → live fetch → stale cache → config fallback.

    ``cfg`` is the ``currency`` config block. ``fetcher`` is injectable for tests.
    """
    now = time.time() if now is None else now
    cache_path = Path(cfg.cache_path)
    cached = _read_cache(cache_path)
    if cached and cfg.auto_update:
        age_hours = (now - (cached.fetched_at or 0)) / 3600
        if age_hours < cfg.max_age_hours:
            return cached

    if cfg.auto_update:
        try:
            to_eur = fetcher(cfg.provider_url)
            if to_eur:
                fx = FxRates(target=cfg.target, to_eur=to_eur, fetched_at=now, source="live")
                _write_cache(cache_path, fx)
                return fx
        except Exception:  # noqa: BLE001 - any fetch failure falls back to cache/config
            if cached:
                return cached

    return FxRates(target=cfg.target, to_eur=dict(cfg.fallback_rates), source="fallback")


# --- process-global active rates ---------------------------------------------------

_ACTIVE: FxRates | None = None


def active() -> FxRates | None:
    """The rates the margin code converts with (None = conversion disabled, e.g. in unit tests)."""
    return _ACTIVE


def set_active(fx: FxRates | None) -> None:
    global _ACTIVE
    _ACTIVE = fx
