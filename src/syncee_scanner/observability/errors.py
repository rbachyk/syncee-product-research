"""Canonical error codes and the exception hierarchy (spec §34).

Every raised failure carries an :class:`ErrorCode` so that logs, debug artifacts and
CLI output all speak the same vocabulary. Retry policy (spec §34.1) is expressed via
:attr:`ScannerError.retryable` so callers never hard-code which errors to retry.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Required error codes from spec §34."""

    AUTH_SESSION_EXPIRED = "AUTH_SESSION_EXPIRED"
    PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    ACCESS_DENIED = "ACCESS_DENIED"
    PAGINATION_LOOP_DETECTED = "PAGINATION_LOOP_DETECTED"
    PRODUCT_PARSE_FAILED = "PRODUCT_PARSE_FAILED"
    SUPPLIER_PARSE_FAILED = "SUPPLIER_PARSE_FAILED"
    NETWORK_RESPONSE_CHANGED = "NETWORK_RESPONSE_CHANGED"
    SOURCE_API_ERROR = "SOURCE_API_ERROR"  # a REST source (CJ/BigBuy/…) API failure
    BASEROW_API_ERROR = "BASEROW_API_ERROR"
    BASEROW_AUTH_ERROR = "BASEROW_AUTH_ERROR"
    BASEROW_SCHEMA_MISMATCH = "BASEROW_SCHEMA_MISMATCH"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INCREMENTAL_ORDER_UNVERIFIED = "INCREMENTAL_ORDER_UNVERIFIED"
    CHECKPOINT_ERROR = "CHECKPOINT_ERROR"
    LLM_API_ERROR = "LLM_API_ERROR"
    IMAGE_PROCESSING_ERROR = "IMAGE_PROCESSING_ERROR"


# Errors that are safe to retry with backoff (spec §34.1). Everything else is terminal.
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.PAGE_LOAD_TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.BASEROW_API_ERROR,
        ErrorCode.NETWORK_RESPONSE_CHANGED,
        ErrorCode.SOURCE_API_ERROR,
        ErrorCode.LLM_API_ERROR,
    }
)


class ScannerError(Exception):
    """Base class for all scanner failures.

    Args:
        code: the canonical error code (spec §34).
        message: human-readable detail; must never contain secrets.
        context: optional structured detail attached to logs/artifacts.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        context: dict | None = None,
    ) -> None:
        self.code = code
        self.context = context or {}
        super().__init__(message or code.value)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def to_dict(self) -> dict:
        return {
            "error_code": self.code.value,
            "message": str(self),
            "context": self.context,
            "retryable": self.retryable,
        }


# --- Specific subclasses (only for codes worth catching by type) --------------------


class AuthError(ScannerError):
    def __init__(self, message: str = "Syncee session is expired or invalid", **kw):
        super().__init__(ErrorCode.AUTH_SESSION_EXPIRED, message, **kw)


class ConfigurationError(ScannerError):
    def __init__(self, message: str, **kw):
        super().__init__(ErrorCode.CONFIGURATION_ERROR, message, **kw)


class PaginationLoopError(ScannerError):
    def __init__(self, message: str = "Pagination cursor/page repeated unexpectedly", **kw):
        super().__init__(ErrorCode.PAGINATION_LOOP_DETECTED, message, **kw)


class BaserowError(ScannerError):
    """Baserow API failure (transient); use :class:`BaserowAuthError` for auth."""

    def __init__(self, message: str, **kw):
        super().__init__(ErrorCode.BASEROW_API_ERROR, message, **kw)


class BaserowAuthError(ScannerError):
    def __init__(self, message: str = "Baserow authentication failed", **kw):
        super().__init__(ErrorCode.BASEROW_AUTH_ERROR, message, **kw)


class BaserowSchemaMismatch(ScannerError):
    def __init__(self, message: str, **kw):
        super().__init__(ErrorCode.BASEROW_SCHEMA_MISMATCH, message, **kw)


class CheckpointError(ScannerError):
    def __init__(self, message: str, **kw):
        super().__init__(ErrorCode.CHECKPOINT_ERROR, message, **kw)
