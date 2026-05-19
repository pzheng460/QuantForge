"""Tests for the LIVE-mode safety gate on /api/live/start.

The frontend modal asks the user to type the strategy name to confirm; the
backend MUST re-validate so a curl caller can't bypass the prompt and
accidentally enter LIVE.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _payload(**overrides):
    base = {
        "strategy": "ema_crossover",
        "exchange": "okx",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "demo": True,
        "position_size_usdt": 100.0,
        "leverage": 1,
        "warmup_bars": 50,
    }
    base.update(overrides)
    return base


def test_demo_mode_does_not_require_confirm_live(client):
    """demo=True must NOT be rejected by the confirm_live safety gate.

    The real engine startup may fail downstream (ccxt sandbox auth, etc.)
    but the LIVE-mode safety gate at the top of /live/start must never
    fire on a demo request. We assert that any error response is for some
    other reason, never the confirm_live one.
    """
    r = client.post("/api/live/start", json=_payload(demo=True))
    if r.status_code >= 400:
        # Any other failure (ccxt auth, missing keys, etc.) is fine here —
        # we only care that the safety gate didn't fire on demo.
        body = r.text.lower()
        assert "confirm_live" not in body
        assert "live mode" not in body


def test_live_mode_without_confirm_is_rejected(client):
    r = client.post("/api/live/start", json=_payload(demo=False))
    assert r.status_code == 400
    assert "confirm_live" in r.json()["detail"]


def test_live_mode_with_wrong_confirm_is_rejected(client):
    r = client.post(
        "/api/live/start",
        json=_payload(demo=False, confirm_live="wrong_name"),
    )
    assert r.status_code == 400
    assert "confirm_live" in r.json()["detail"]


def test_live_mode_requires_strategy_name_not_inline_pine(client):
    """demo=false + pine_source (no strategy name) should be refused."""
    payload = _payload(demo=False, confirm_live="anything")
    del payload["strategy"]
    payload["pine_source"] = "strategy('inline')"
    r = client.post("/api/live/start", json=payload)
    assert r.status_code == 400
    assert "named strategy" in r.json()["detail"].lower()
