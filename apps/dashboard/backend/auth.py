"""API-key authentication shared by the HTTP middleware and WebSocket handlers.

Starlette's ``@app.middleware("http")`` does NOT intercept WebSocket
connections, so an API key enforced only there leaves every ``/ws/*`` stream
open to the network. WebSocket handlers therefore call
:func:`websocket_api_key_authorized` themselves and refuse the handshake when
the key is set but missing/invalid (browsers can only pass the key to a WS
upgrade as a query parameter).

When ``QUANTFORGE_API_KEY`` is unset (loopback-only operation) every check
passes, matching the HTTP middleware contract.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger(__name__)

_RAW_KEY = os.environ.get("QUANTFORGE_API_KEY", "")
#: Auth is keyed off the TRIMMED value: "   " is not a key. If the operator
#: exported a whitespace-only value, surface it loudly instead of silently
#: running with auth disabled while believing it is enabled.
_QUANTFORGE_API_KEY = _RAW_KEY.strip()
if _RAW_KEY and not _QUANTFORGE_API_KEY:
    logger.warning(
        "QUANTFORGE_API_KEY is whitespace-only — API-key auth is DISABLED; "
        "refusing to run exposed (CLI/start.sh guard non-loopback binds)."
    )


def api_key_configured() -> bool:
    return bool(_QUANTFORGE_API_KEY)


def api_key_authorized(provided: str | None) -> bool:
    """Constant-time comparison; an unset server key disables the check."""
    if not _QUANTFORGE_API_KEY:
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided, _QUANTFORGE_API_KEY)


def request_api_key_authorized(request) -> bool:
    """HTTP request: key in the ``X-API-Key`` header or ``api_key`` query."""
    if not _QUANTFORGE_API_KEY:
        return True
    provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    return api_key_authorized(provided)


def websocket_api_key_authorized(websocket) -> bool:
    """WebSocket handshake: key must arrive as the ``api_key`` query param."""
    if not _QUANTFORGE_API_KEY:
        return True
    return api_key_authorized(websocket.query_params.get("api_key"))
