"""Structured logging setup (spec §35).

Emits both a human-readable console/file stream (``logs/scanner.log``) and a
machine-readable JSON lines stream (``logs/scanner.jsonl``). Log records carry the
context fields listed in spec §35 (run_id, command, page, cursor, product_key,
supplier_key, operation, duration, result, error_code) whenever a caller binds them.

Usage::

    from syncee_scanner.observability.logging import configure_logging, get_logger

    configure_logging(debug=False)
    log = get_logger(__name__).bind(run_id=run_id, command="scan.full")
    log.info("page.processed", page=12, products=100, duration=1.4)
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import structlog

DEFAULT_LOG_DIR = Path("logs")

# Context keys we expect to see bound on records (spec §35). Documented, not enforced.
CONTEXT_FIELDS = (
    "run_id",
    "command",
    "page",
    "cursor",
    "product_key",
    "supplier_key",
    "operation",
    "duration",
    "result",
    "error_code",
)

_configured = False


def configure_logging(
    *,
    debug: bool = False,
    log_dir: Path | str = DEFAULT_LOG_DIR,
    json_lines: bool = True,
) -> None:
    """Configure structlog + stdlib logging once per process.

    Args:
        debug: when True, sets DEBUG level and pretty console rendering.
        log_dir: directory for ``scanner.log`` and ``scanner.jsonl``.
        json_lines: also write a JSONL stream for machine consumption.
    """
    global _configured
    if _configured:
        return

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console — readable text.
    console = logging.StreamHandler()
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    root.addHandler(console)

    # File — human-readable rotating log.
    text_file = logging.handlers.RotatingFileHandler(
        log_dir / "scanner.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    text_file.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    root.addHandler(text_file)

    # File — JSON lines for machines.
    if json_lines:
        json_file = logging.handlers.RotatingFileHandler(
            log_dir / "scanner.jsonl", maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
        json_file.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
                foreign_pre_chain=shared_processors,
            )
        )
        root.addHandler(json_file)

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Call :func:`configure_logging` first."""
    return structlog.stdlib.get_logger(name)


def reset_logging_for_tests() -> None:
    """Allow tests to reconfigure logging."""
    global _configured
    _configured = False
