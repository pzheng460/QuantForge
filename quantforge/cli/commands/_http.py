"""Tiny HTTP client for the QuantForge web server.

Some CLI commands need to talk to the running FastAPI app (e.g. to manage
running asyncio tasks the server owns). Stateless commands should read
filesystem/library directly instead — server availability would otherwise
become a hard dependency for trivial actions like `strategies list`.
"""

from __future__ import annotations

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


def _request(method, url: str, timeout: float, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(_auth_headers())
    try:
        r = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
    except requests.ConnectionError as e:
        raise ServerUnreachable(f"Cannot reach {url} — is the web server running?") from e
    r.raise_for_status()
    return r.json()


class ServerUnreachable(RuntimeError):
    """Raised when the QuantForge web server isn't responding."""


def get(path: str, **kwargs):
    timeout = kwargs.pop("timeout", 10)
    return _request("GET", base_url() + path, timeout, **kwargs)


def post(path: str, **kwargs):
    timeout = kwargs.pop("timeout", 30)
    return _request("POST", base_url() + path, timeout, **kwargs)
