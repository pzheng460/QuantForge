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
        "exchange": "bitget",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "warmup_bars": 50,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "override",
    [
        {"warmup_bars": -1},
        {"timeframe": "2m"},
        {"start_date": "2026-01-02", "end_date": "2026-01-02"},
        {"strategy": ""},
    ],
)
def test_backtest_request_validation(client, override):
    assert client.post("/api/backtest/run", json=_payload(**override)).status_code == 422


def test_registered_strategy_config_rejects_unknown_or_invalid_values():
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy

    config = get_strategy("ema_crossover").config_model
    with pytest.raises(ValueError):
        config(fast_period=20, slow_period=10)
    with pytest.raises(ValueError):
        config(unknown_parameter=1)
