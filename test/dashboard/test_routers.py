"""Critical-path tests for apps.dashboard.backend FastAPI routers.

These tests exercise the API surface that the frontend depends on without
hitting the network (no real OHLCV fetches, no Pine optimisation, no agent
subprocesses). They verify shapes, 404 paths, and that schema endpoints
return real on-disk strategies.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ─── Health / static metadata ────────────────────────────────────────────────


def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_exchanges_returns_known_set(client):
    r = client.get("/api/exchanges")
    assert r.status_code == 200
    body = r.json()
    ids = {e["id"] for e in body}
    # These are the five we support via ccxt; if any disappear something
    # changed in the exchanges router and the frontend dropdown breaks.
    assert {"bitget", "binance", "okx", "bybit", "hyperliquid"}.issubset(ids)
    for e in body:
        assert {"id", "name", "default_symbol", "maker_fee", "taker_fee"} <= set(e)


# ─── Strategies router ───────────────────────────────────────────────────────


def test_strategies_list_nonempty_and_well_formed(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0
    sample = body[0]
    assert {
        "name",
        "display_name",
        "config_fields",
        "config_schema",
        "engine",
    } <= set(sample)
    assert sample["engine"] == "python"
    assert any(len(s["config_fields"]) > 0 for s in body)


def test_strategy_by_name_404_on_missing(client):
    r = client.get("/api/strategies/__definitely_not_a_strategy__")
    assert r.status_code == 404


def test_strategy_source_is_not_exposed(client):
    list_r = client.get("/api/strategies")
    name = list_r.json()[0]["name"]
    r = client.get(f"/api/strategies/{name}/source")
    assert r.status_code == 404
    assert "not available" in r.json()["detail"]


# ─── Agent router ────────────────────────────────────────────────────────────


def test_unknown_api_route_does_not_fall_through_to_spa(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "API route not found"


# ─── Optimize router ─────────────────────────────────────────────────────────


def test_optimize_status_404_on_unknown_job(client):
    r = client.get("/api/optimize/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_optimize_cancel_404_on_unknown_job(client):
    r = client.post("/api/optimize/cancel/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# ─── Live router ─────────────────────────────────────────────────────────────


def test_live_engines_list_returns_list(client):
    r = client.get("/api/live/engines")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_live_engine_delete_404_on_unknown(client):
    r = client.delete("/api/live/engines/does-not-exist-zzz")
    assert r.status_code == 404


def test_live_engine_delete_409_on_active(client, monkeypatch):
    """Refuse to delete a running/warmup engine — operator must stop it first."""
    import apps.dashboard.backend.live_engines as le

    monkeypatch.setitem(
        le._engines,
        "fake-active-eid",
        {
            "engine": None,
            "task": None,
            "status": "running",
            "strategy": "s",
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "demo": True,
            "leverage": 1,
            "position_size_usdt": 10.0,
            "warmup_bars": 50,
            "created_at": "2026-05-19T00:00:00+00:00",
            "stopped_at": None,
            "error": None,
        },
    )
    try:
        r = client.delete("/api/live/engines/fake-active-eid")
        assert r.status_code == 409
        assert "still" in r.json()["detail"].lower()
    finally:
        le._engines.pop("fake-active-eid", None)


def test_live_engine_delete_200_on_archived(client, monkeypatch):
    """Archived (stopped) engines can be deleted."""
    import apps.dashboard.backend.live_engines as le

    # Avoid actually writing to disk during the test.
    monkeypatch.setattr(le, "_save_state", lambda: None)
    monkeypatch.setitem(
        le._engines,
        "fake-archived-eid",
        {
            "engine": None,
            "task": None,
            "status": "stopped",
            "strategy": "s",
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "demo": True,
            "leverage": 1,
            "position_size_usdt": 10.0,
            "warmup_bars": 50,
            "created_at": "2026-05-19T00:00:00+00:00",
            "stopped_at": "2026-05-19T01:00:00+00:00",
            "error": None,
        },
    )
    r = client.delete("/api/live/engines/fake-archived-eid")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert "fake-archived-eid" not in le._engines
