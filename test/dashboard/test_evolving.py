"""Tests for Evolving Mode state and HTTP routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from quantforge import evolving
from quantforge.evolving import switch


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(switch, "STATE_PATH", tmp_path / "evolving.json")


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def test_default_is_off():
    assert evolving.is_enabled() is False
    assert evolving.is_enabled("ema_crossover") is False


def test_enable_with_strategy_allowlist_excludes_others():
    evolving.enable(["ema_crossover"])
    assert evolving.is_enabled("ema_crossover") is True
    assert evolving.is_enabled("other") is False


def test_enable_with_empty_allowlist_covers_all():
    evolving.enable([])
    assert evolving.is_enabled("any_strategy") is True


def test_disable_preserves_allowlist():
    evolving.enable(["s1", "s2"])
    evolving.disable()
    assert evolving.load_state()["strategies"] == ["s1", "s2"]


def test_add_and_remove_strategy():
    evolving.enable()
    evolving.add_strategy("new")
    evolving.add_strategy("new")
    assert "new" in evolving.load_state()["strategies"]
    evolving.remove_strategy("new")
    assert "new" not in evolving.load_state()["strategies"]


def test_corrupt_state_file_falls_back_to_default(tmp_path, monkeypatch):
    path = tmp_path / "evolving.json"
    path.write_text("{not json")
    monkeypatch.setattr(switch, "STATE_PATH", path)
    state = evolving.load_state()
    assert state["enabled"] is False
    assert state["strategies"] == []


def test_evolving_http_routes(client):
    assert client.get("/api/bot/evolving").json()["enabled"] is False
    assert client.post(
        "/api/bot/evolving", json={"enabled": True}
    ).json()["enabled"] is True
    response = client.post(
        "/api/bot/evolving", json={"add_strategies": ["alpha", "beta"]}
    )
    assert set(response.json()["strategies"]) == {"alpha", "beta"}


def test_bot_status_includes_control_state(client):
    body = client.get("/api/bot/status").json()
    assert "evolving" in body
    assert isinstance(body["control_state"], list)
