"""LiveStartRequest.warmup_bars must be bounded like backtest/optimize.

Without a ceiling a client could pass warmup_bars=999_999_999 and trigger a
runaway historical prefetch on a live engine. The schema's Field(ge=0, le=10000)
turns an over-cap request into a clean 422, matching BacktestRequest and
OptimizeRequest which already enforce the same 0..10000 bound.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from apps.dashboard.backend.models import LiveStartRequest


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


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def test_warmup_bars_model_rejects_above_10000():
    with pytest.raises(Exception):
        LiveStartRequest(**_payload(warmup_bars=999_999_999))


def test_warmup_bars_model_rejects_negative():
    with pytest.raises(Exception):
        LiveStartRequest(**_payload(warmup_bars=-1))


def test_warmup_bars_model_accepts_bounds():
    assert LiveStartRequest(**_payload(warmup_bars=0)).warmup_bars == 0
    assert LiveStartRequest(**_payload(warmup_bars=10000)).warmup_bars == 10000


def test_live_start_rejects_absurd_warmup_bars(client, monkeypatch):
    """The /live/start endpoint must reject warmup_bars above the server-side
    ceiling with 422 before any engine is built (no runaway prefetch)."""
    import apps.dashboard.backend.live_engines as live_engines

    started: list = []
    monkeypatch.setattr(
        live_engines, "list_engines", lambda: []
    )
    monkeypatch.setattr(
        live_engines,
        "start_engine",
        lambda **kwargs: started.append(kwargs) or "fake-engine",
    )

    r = client.post("/api/live/start", json=_payload(warmup_bars=999_999_999))
    assert r.status_code == 422
    assert not started  # the engine was never started


def test_live_start_rejects_negative_warmup_bars(client, monkeypatch):
    import apps.dashboard.backend.live_engines as live_engines

    monkeypatch.setattr(live_engines, "list_engines", lambda: [])
    monkeypatch.setattr(
        live_engines, "start_engine", lambda **kwargs: "fake-engine"
    )

    r = client.post("/api/live/start", json=_payload(warmup_bars=-5))
    assert r.status_code == 422
