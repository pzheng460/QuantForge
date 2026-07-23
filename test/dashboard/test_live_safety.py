"""Contracts for registered Python live strategies and hard risk parameters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def _payload(**overrides):
    payload = {
        "strategy": "ema_crossover",
        "exchange": "okx",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "demo": True,
        "position_size_usdt": 100.0,
        "leverage": 1,
        "warmup_bars": 50,
    }
    payload.update(overrides)
    return payload


def _mock_manager(monkeypatch, *, demo: bool):
    import apps.dashboard.backend.live_engines as live_engines

    captured = {}

    async def fake_start_engine(**kwargs):
        captured.update(kwargs)
        return "fake-engine"

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
            "demo": demo,
            "leverage": 1,
            "created_at": "2026-05-25T00:00:00+00:00",
        },
    )
    return captured


def test_live_start_needs_no_per_order_or_typed_confirmation(client, monkeypatch):
    captured = _mock_manager(monkeypatch, demo=False)

    response = client.post("/api/live/start", json=_payload(demo=False))

    assert response.status_code == 200
    assert captured["strategy"] == "ema_crossover"
    assert "pine_source" not in captured


def test_live_start_forwards_non_bypassable_risk_limits(client, monkeypatch):
    captured = _mock_manager(monkeypatch, demo=True)

    response = client.post(
        "/api/live/start",
        json=_payload(
            max_order_notional=750,
            max_leverage=2,
            max_daily_new_positions=3,
        ),
    )

    assert response.status_code == 200
    assert captured["risk_limits"] == {
        "max_order_notional": 750.0,
        "max_spread_pct": 0.15,
        "max_leverage": 2.0,
        "max_daily_new_positions": 3,
    }


def test_live_start_rejects_inline_source_and_missing_strategy(client):
    response = client.post(
        "/api/live/start",
        json={**_payload(), "strategy": None, "pine_source": "strategy('inline')"},
    )

    assert response.status_code == 422
