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
    return os.environ.get("QF_API_URL", "http://127.0.0.1:8000").rstrip("/")


class ServerUnreachable(RuntimeError):
    """Raised when the QuantForge web server isn't responding."""


def get(path: str, **kwargs):
    url = base_url() + path
    try:
        r = requests.get(url, timeout=kwargs.pop("timeout", 10), **kwargs)
    except requests.ConnectionError as e:
        raise ServerUnreachable(
            f"Cannot reach {url} — is the web server running? "
            f"(start with `uvicorn web.backend.main:app --reload`)"
        ) from e
    r.raise_for_status()
    return r.json()


def post(path: str, **kwargs):
    url = base_url() + path
    try:
        r = requests.post(url, timeout=kwargs.pop("timeout", 30), **kwargs)
    except requests.ConnectionError as e:
        raise ServerUnreachable(
            f"Cannot reach {url} — is the web server running?"
        ) from e
    r.raise_for_status()
    return r.json()
