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
    assert len(body) > 0, "expected at least one .pine file under quantforge/pine/strategies/"
    sample = body[0]
    assert {"name", "display_name", "config_fields"} <= set(sample)
    # config_fields is the list of parsed input.int/input.float params; at
    # least one well-known strategy should have a populated one.
    assert any(len(s["config_fields"]) > 0 for s in body), (
        "no strategy has parsed input parameters — Pine parser path is broken"
    )


def test_strategy_by_name_404_on_missing(client):
    r = client.get("/api/strategies/__definitely_not_a_strategy__")
    assert r.status_code == 404


def test_strategy_source_returns_pine_declaration(client):
    list_r = client.get("/api/strategies")
    name = list_r.json()[0]["name"]
    r = client.get(f"/api/strategies/{name}/source")
    assert r.status_code == 200
    source = r.json()["source"]
    # Every valid Pine strategy file starts with `strategy("...")` somewhere.
    assert "strategy(" in source


# ─── Agent router ────────────────────────────────────────────────────────────

def test_agent_skills_returns_list(client):
    r = client.get("/api/agent/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_agent_status_404_on_unknown_job(client):
    r = client.get("/api/agent/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


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
