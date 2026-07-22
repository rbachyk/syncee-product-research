"""Value normalization (spec §18).

Pure, dependency-free functions applied before persistence so that keys, fingerprints
and scoring are deterministic. Original values are preserved by callers in ``Raw Data``
(spec §18.1/§18.2); these helpers never mutate their inputs.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --- Text --------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str | None:
    """Trim, collapse whitespace and NFC-normalize Unicode (spec §18.1)."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}


def strip_html(value: str | None) -> str | None:
    """Strip HTML tags/entities from a description and collapse whitespace (spec §18.1)."""
    if value is None:
        return None
    text = _HTML_TAG.sub(" ", str(value))
    for entity, repl in _HTML_ENTITY.items():
        text = text.replace(entity, repl)
    return normalize_text(text)


def slugify(value: str | None) -> str:
    """Lowercase ASCII slug used for deterministic hashing of names."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


# --- URL ---------------------------------------------------------------------------

# Tracking params stripped during normalization (spec §18.2).
_TRACKING_PREFIXES = ("utm_", "mc_", "pk_")
_TRACKING_EXACT = {
    "gclid",
    "fbclid",
    "ref",
    "referrer",
    "source",
    "_ga",
    "yclid",
    "msclkid",
}


def normalize_url(value: str | None) -> str | None:
    """Canonicalize a URL: lowercase host, strip tracking params, drop trailing slash.

    Preserves the canonical route and query ordering (sorted) so the same logical URL
    always yields the same string (spec §18.2). Returns None for empty/invalid input.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme and not parts.netloc:
        # Relative or bare value — return trimmed, no further canonicalization.
        return raw.rstrip("/") or None

    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not (k.lower() in _TRACKING_EXACT or k.lower().startswith(_TRACKING_PREFIXES))
    ]
    query = urlencode(sorted(kept))

    path = parts.path
    if len(path) > 1:
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))  # fragment dropped


# --- Country -----------------------------------------------------------------------

# Minimal alias map -> canonical English name. Extend as discovery reveals variants.
_COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "deutschland": "Germany",
    "espana": "Spain",
    "españa": "Spain",
    "italia": "Italy",
    "nederland": "Netherlands",
    "the netherlands": "Netherlands",
    "holland": "Netherlands",
    "osterreich": "Austria",
    "österreich": "Austria",
    "belgie": "Belgium",
    "belgique": "Belgium",
    "eire": "Ireland",
    "czechia": "Czech Republic",
    "czech republic": "Czech Republic",
}


def normalize_country(value: str | None) -> str | None:
    """Normalize a country label to a consistent canonical name (spec §18.3)."""
    text = normalize_text(value)
    if not text:
        return None
    key = text.lower().strip(". ")
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    # Title-case fallback, preserving already-consistent names.
    return text if text[:1].isupper() else text.title()


def normalize_country_list(values) -> list[str]:
    """Normalize + dedupe a list (or delimited string) of countries, order-stable."""
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[;,|/]", values)
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        c = normalize_country(v)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# --- Price -------------------------------------------------------------------------

_PRICE_CLEAN = re.compile(r"[^\d,.\-]")


def normalize_price(value) -> float | None:
    """Parse a price string/number to float, handling ``.``/``,`` separators (spec §18.4).

    Currency is *not* converted (spec §18.4) — callers keep the currency separately.
    Returns None for missing/invalid values (which callers mark as unknown).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _PRICE_CLEAN.sub("", str(value)).strip()
    if not text or text in {"-", ".", ","}:
        return None

    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # The right-most separator is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        # Comma as decimal (e.g. "12,50") vs thousands ("1,250").
        if len(text.split(",")[-1]) == 3 and text.count(",") == 1:
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


# --- Date --------------------------------------------------------------------------


def normalize_datetime(value) -> str | None:
    """Return an ISO-8601 UTC timestamp string, or None (spec §18.5).

    Accepts datetime/date objects, epoch seconds, and common ISO strings.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=UTC)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (for First/Last Seen At)."""
    return datetime.now(tz=UTC).isoformat()


# --- Boolean -----------------------------------------------------------------------

_TRUE = {"true", "yes", "y", "1", "available", "in stock", "instock", "enabled", "active"}
_FALSE = {"false", "no", "n", "0", "unavailable", "out of stock", "disabled", "inactive"}


def normalize_bool(value) -> bool | None:
    """Convert a source label to True/False/None (unknown) (spec §18.6)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None
