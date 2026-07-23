"""Source factory: build a product source by name from config (multi-source support).

Each source in ``config.sources`` names a mapping YAML + a transport. This turns a source
name into a ready :class:`ProductSource` plus the metadata (label + key prefix) the scan uses
to stamp/​namespace rows, so several sources (Syncee, CJ, BigBuy, …) coexist without colliding.
Adding a source is config only — no changes here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..observability.errors import ErrorCode, ScannerError
from .mapper import SynceeResponseMapper, load_mapping
from .source import FixtureSource, SynceeSource


@dataclass(frozen=True)
class SourceMeta:
    """What the scan needs to tag a source's rows."""

    name: str
    label: str
    key_prefix: str = ""

    def key(self, base: str) -> str:
        """Namespace a raw key so sources never collide ('' prefix = unchanged)."""
        return f"{self.key_prefix}:{base}" if self.key_prefix else base


def _source_config(cfg: AppConfig, source_name: str | None):
    name = source_name or cfg.default_source
    src = cfg.sources.get(name)
    if src is None:
        known = ", ".join(sorted(cfg.sources)) or "(none configured)"
        raise ScannerError(
            ErrorCode.CONFIGURATION_ERROR, f"Unknown source '{name}'. Configured: {known}."
        )
    return name, src


def build_transport(cfg: AppConfig, source_name: str | None = None):
    """Return ``(transport, mapper, SourceMeta)`` for the named source.

    Used for both scanning (page transport) and enrichment (the same transport exposes
    ``get_detail``), so a source's detail lookups hit the right API.
    """
    name, src = _source_config(cfg, source_name)
    meta = SourceMeta(name=name, label=src.label, key_prefix=src.key_prefix)
    mapping = load_mapping(src.mapping)
    mapper = SynceeResponseMapper(mapping)
    if src.transport == "syncee":
        from ..browser.transport import SynceeApiTransport
        transport = SynceeApiTransport(cfg, mapping)
    else:  # "rest" — CJ / BigBuy / any API source
        from .rest_transport import RestApiTransport
        transport = RestApiTransport(src.api, mapping)
    return transport, mapper, meta


def build_source(cfg: AppConfig, source_name: str | None = None, *, fixture: str | None = None):
    """Return ``(source, SourceMeta)`` for the named source (default: ``cfg.default_source``).

    ``fixture`` forces an offline :class:`FixtureSource` (keeps the source's label/prefix).
    """
    name, src = _source_config(cfg, source_name)
    meta = SourceMeta(name=name, label=src.label, key_prefix=src.key_prefix)
    if fixture:
        return FixtureSource.from_file(Path(fixture)), meta
    transport, mapper, meta = build_transport(cfg, source_name)
    return SynceeSource(cfg, transport=transport, mapper=mapper), meta
