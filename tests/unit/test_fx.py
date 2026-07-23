"""FX rate loading + EUR conversion (spec §23)."""

from __future__ import annotations

import json

from syncee_scanner.config import CurrencyConfig
from syncee_scanner.pricing import fx as fxmod


def _cfg(tmp_path, **kw):
    return CurrencyConfig(cache_path=str(tmp_path / "fx.json"), **kw)


def test_convert_eur_is_identity():
    r = fxmod.FxRates(to_eur={"USD": 0.9})
    assert r.convert(10, "EUR") == 10
    assert r.convert(10, None) == 10  # missing currency assumed target


def test_convert_foreign_and_unknown():
    r = fxmod.FxRates(to_eur={"HUF": 0.0026})
    assert r.convert(4000, "HUF") == 4000 * 0.0026
    assert r.convert(4000, "huf") == 4000 * 0.0026  # case-insensitive
    assert r.convert(10, "XYZ") is None            # unknown currency
    assert r.convert(None, "HUF") is None


def test_live_rates_convert(tmp_path):
    # fetcher returns EUR-per-unit (what _fetch_frankfurter produces after inversion).
    rates = fxmod.load_rates(
        _cfg(tmp_path),
        fetcher=lambda url: {"USD": 1 / 1.25, "HUF": 1 / 400, "EUR": 1.0},
        now=1000.0,
    )
    assert rates.source == "live"
    assert round(rates.convert(100, "USD"), 2) == 80.0    # 100 USD @1.25/EUR = 80 EUR
    assert round(rates.convert(400, "HUF"), 2) == 1.0


def test_cache_used_when_fresh(tmp_path):
    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return {"USD": 0.9, "EUR": 1.0}

    fxmod.load_rates(cfg, fetcher=fetch, now=1000.0)          # writes cache
    fxmod.load_rates(cfg, fetcher=fetch, now=1000.0 + 3600)   # 1h later, still fresh
    assert calls["n"] == 1  # second call served from cache, no refetch


def test_stale_cache_triggers_refetch(tmp_path):
    cfg = _cfg(tmp_path, max_age_hours=24)
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return {"USD": 0.9, "EUR": 1.0}

    fxmod.load_rates(cfg, fetcher=fetch, now=1000.0)
    fxmod.load_rates(cfg, fetcher=fetch, now=1000.0 + 25 * 3600)  # >24h later
    assert calls["n"] == 2


def test_fetch_failure_falls_back_to_config(tmp_path):
    cfg = _cfg(tmp_path, fallback_rates={"USD": 0.5, "EUR": 1.0})

    def boom(url):
        raise RuntimeError("network down")

    rates = fxmod.load_rates(cfg, fetcher=boom, now=1000.0)
    assert rates.source == "fallback"
    assert rates.convert(10, "USD") == 5.0


def test_fetch_failure_prefers_stale_cache_over_fallback(tmp_path):
    cfg = _cfg(tmp_path, max_age_hours=1, fallback_rates={"USD": 0.5, "EUR": 1.0})
    fxmod.load_rates(cfg, fetcher=lambda url: {"USD": 0.9, "EUR": 1.0}, now=1000.0)  # cache

    def boom(url):
        raise RuntimeError("network down")

    rates = fxmod.load_rates(cfg, fetcher=boom, now=1000.0 + 10 * 3600)  # stale, fetch fails
    assert rates.source == "cache"
    assert rates.convert(10, "USD") == 9.0  # stale cache, not the 0.5 fallback


def test_cache_file_is_written(tmp_path):
    cfg = _cfg(tmp_path)
    fxmod.load_rates(cfg, fetcher=lambda url: {"USD": 0.9, "EUR": 1.0}, now=1234.0)
    saved = json.loads((tmp_path / "fx.json").read_text())
    assert saved["to_eur"]["USD"] == 0.9
    assert saved["fetched_at"] == 1234.0
