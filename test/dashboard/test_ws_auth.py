"""WebSocket API-key enforcement.

The HTTP middleware never sees WebSocket upgrades in Starlette, so the WS
handlers must enforce the key themselves. These tests pin that behavior:
with QUANTFORGE_API_KEY set, any /api/ws/* upgrade without the correct
``api_key`` query parameter must be refused at the handshake.
"""

from __future__ import annotations

import pytest

import apps.dashboard.backend.auth as auth_module
from apps.dashboard.backend.main import app

API_KEY = "test-secret-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_module, "_QUANTFORGE_API_KEY", API_KEY)
    from fastapi.testclient import TestClient

    with TestClient(app) as value:
        yield value


def _refused_or_closed(ws_context):
    """Return True if the handshake was refused or immediately closed.

    The close may surface either on connect (``__enter__``) or on the first
    receive; a denied handshake may also raise WebSocketDenialResponse. Accept
    any of those paths as success, as long as a message never arrives.
    """
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    try:
        with ws_context as ws:
            try:
                ws.receive_json()
            except WebSocketDisconnect as exc:
                return exc.code == 4401
            return False  # a message arrived — connection was accepted
    except WebSocketDenialResponse:
        return True
    except WebSocketDisconnect as exc:
        return exc.code == 4401


def test_ws_backtest_refused_without_key(client):
    assert _refused_or_closed(
        client.websocket_connect("/api/ws/backtest/does-not-exist")
    )


def test_ws_optimize_refused_without_key(client):
    assert _refused_or_closed(
        client.websocket_connect("/api/ws/optimize/does-not-exist")
    )


def test_ws_backtest_accepted_with_valid_key(client):
    with client.websocket_connect(
        f"/api/ws/backtest/does-not-exist?api_key={API_KEY}"
    ) as ws:
        msg = ws.receive_json()
    assert msg["status"] == "not_found"
