"""Tiny HTTP client for the QuantForge web server.

Some CLI commands need to talk to the running FastAPI app (e.g. to manage
running asyncio tasks the server owns). Stateless commands should read
filesystem/library directly instead — server availability would otherwise
become a hard dependency for trivial actions like `strategies list`.
"""

from __future__ import annotations

import json
import os

import requests


def base_url() -> str:
    return os.environ.get("QF_API_URL", "http://127.0.0.1:8000/api").rstrip("/")


def _auth_headers() -> dict:
    """Forward QUANTFORGE_API_KEY to the server.

    A non-loopback (or key-guarded) dashboard requires ``X-API-Key``; without
    forwarding, CLI commands that talk to the running server (e.g.
    ``web``/engine-managing commands) 401 whenever the dashboard is bound
    beyond loopback with auth enabled. The value is trimmed exactly like the
    backend's auth module so a whitespace-only env var contributes nothing.
    """
    key = (os.environ.get("QUANTFORGE_API_KEY") or "").strip()
    return {"X-API-Key": key} if key else {}


class ServerUnreachable(RuntimeError):
    """Raised when the QuantForge web server isn't responding."""


class ServerError(RuntimeError):
    """Raised when the server answers with a non-2xx status.

    Carries a stable, sanitized hint derived from the response body (FastAPI
    validation errors / detail strings) instead of a raw requests traceback.
    """


def _request(method, url: str, timeout: float, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(_auth_headers())
    try:
        r = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.SSLError,
        requests.exceptions.TooManyRedirects,
        requests.exceptions.InvalidURL,
    ) as e:
        # A down server, stalled connection, TLS failure, or malformed URL
        # must all degrade to the same friendly "unreachable" message instead
        # of a raw requests traceback.
        raise ServerUnreachable(
            f"Cannot reach {url} — is the web server running?"
        ) from e
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        detail = _extract_detail(r)
        if detail:
            raise ServerError(
                f"Server rejected the request ({r.status_code} {r.reason}): {detail}"
            ) from e
        raise ServerError(
            f"Server rejected the request ({r.status_code} {r.reason})"
        ) from e
    return r.json()


def _extract_detail(response) -> str:
    """Best-effort, sanitized error text from a FastAPI error response.

    Never echo raw exception text (which can embed paths or secrets) — only
    the stable ``detail`` FastAPI returns, truncated.
    """
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return ""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail[:200]
        if isinstance(detail, list):  # pydantic validation errors
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = ".".join(
                        str(part) for part in item.get("loc", []) if part != "body"
                    )
                    msg = item.get("msg", "")
                    if loc and msg:
                        parts.append(f"{loc}: {msg}")
            if parts:
                return "; ".join(parts[:5])[:200]
    return ""


def get(path: str, **kwargs):
    timeout = kwargs.pop("timeout", 10)
    return _request("GET", base_url() + path, timeout, **kwargs)


def post(path: str, **kwargs):
    timeout = kwargs.pop("timeout", 30)
    return _request("POST", base_url() + path, timeout, **kwargs)
