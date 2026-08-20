"""Client-safe HTTP error details for the dashboard API.

Raw ``str(exception)`` messages may embed filesystem paths, exchange payloads,
or token fragments and must never be echoed back to API clients. These helpers
surface a stable failure category (exception class name) while the full detail
stays in server logs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def sanitize_exception(exc: BaseException, *, prefix: str) -> str:
    """Client-safe error text for PERSISTED fields (job error, engine error).

    Same contract as :func:`safe_exception_detail` but without logging — the
    caller is expected to have already logged the full detail where it failed.
    """
    return f"{prefix} ({type(exc).__name__})"


def safe_exception_detail(exc: BaseException, *, prefix: str, log_level: int = logging.ERROR) -> str:
    """Return ``"<prefix> (<ExceptionName>)"`` and log the full message.

    ``prefix`` is a static, operator-meaningful category. The exception is
    logged server-side with its real message for diagnosis.
    """
    logger.log(
        log_level,
        "%s: %s: %s",
        prefix,
        type(exc).__name__,
        exc,
        exc_info=bool(log_level >= logging.ERROR),
    )
    return sanitize_exception(exc, prefix=prefix)
