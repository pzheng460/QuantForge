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


def test_demo_mode_does_not_require_confirm_live(client, monkeypatch):
    """demo=True must NOT be rejected by the confirm_live safety gate.

    Mock engine startup so this safety test never creates persisted live
    engine entries or hits real exchange APIs.
    """
    import apps.dashboard.backend.live_engines as live_engines

    async def fake_start_engine(**_kwargs):
        return "fake-demo-engine"

    monkeypatch.setattr(live_engines, "list_engines", lambda: [])
    monkeypatch.setattr(live_engines, "start_engine", fake_start_engine)
    monkeypatch.setattr(
        live_engines,
        "get_engine",
        lambda _engine_id: {
            "status": "warmup",
            "strategy": "ema_crossover",
            "exchange": "okx",
            "symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "demo": True,
            "leverage": 1,
            "created_at": "2026-05-25T00:00:00+00:00",
        },
    )

    r = client.post("/api/live/start", json=_payload(demo=True))
    assert r.status_code == 200
    assert r.json()["engine_id"] == "fake-demo-engine"


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
