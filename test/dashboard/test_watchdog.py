"""Watchdog restart contract for the live-engine lifecycle."""

from __future__ import annotations

import pytest

import apps.dashboard.backend.live_engines as live_engines


def _entry(**overrides):
    entry = {
        "engine_id": "wf1",
        "engine": object(),
        "task": None,
        "status": "restarting",
        "restart_count": 0,
        "pending_restart": None,
        "strategy": "ema_crossover",
        "exchange": "okx",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "demo": True,
        "leverage": 1,
        "position_size_usdt": 100.0,
        "warmup_bars": 50,
        "config_override": None,
        "risk_limits": {},
        "created_at": "2025-01-01T00:00:00+00:00",
        "error": None,
    }
    entry.update(overrides)
    return entry


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Never touch the real ~/.quantforge state or build real connectors.
    monkeypatch.setattr(live_engines, "_save_state", lambda: None)
    monkeypatch.setattr(live_engines, "_engines", {})
    yield


@pytest.mark.asyncio
async def test_watchdog_marks_failed_when_budget_exhausted():
    live_engines._engines["wf1"] = _entry(restart_count=live_engines._MAX_RESTART_ATTEMPTS)

    await live_engines._restart_engine("wf1")

    entry = live_engines._engines["wf1"]
    assert entry["status"] == "failed"
    assert "manual intervention" in entry["error"]


@pytest.mark.asyncio
async def test_watchdog_aborts_when_operator_stopped(monkeypatch):
    entry = _entry()
    live_engines._engines["wf1"] = entry

    async def fake_restart(engine_id):
        raise AssertionError("restart must not run after operator stop")

    monkeypatch.setattr(live_engines, "_build_runtime", fake_restart)
    entry["status"] = "stopped"  # operator stopped while backoff was pending

    await live_engines._restart_engine("wf1")

    assert live_engines._engines["wf1"]["status"] == "stopped"
    assert live_engines._engines["wf1"]["restart_count"] == 0


@pytest.mark.asyncio
async def test_watchdog_fails_when_rebuild_raises(monkeypatch):
    entry = _entry()
    live_engines._engines["wf1"] = entry

    def boom(**kwargs):
        raise RuntimeError("keys missing")

    monkeypatch.setattr(live_engines, "_build_runtime", boom)
    monkeypatch.setattr(
        live_engines, "_RESTART_BACKOFF_BASE_SECONDS", 0.0
    )  # no waiting in tests

    await live_engines._restart_engine("wf1")

    assert live_engines._engines["wf1"]["status"] == "failed"
    assert "watchdog restart failed" in live_engines._engines["wf1"]["error"]


@pytest.mark.asyncio
async def test_watchdog_restarts_successfully(monkeypatch):
    entry = _entry()
    live_engines._engines["wf1"] = entry
    started: list = []

    monkeypatch.setattr(
        live_engines, "_build_runtime", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        live_engines, "_start_task", lambda e: started.append(e)
    )
    monkeypatch.setattr(
        live_engines, "_RESTART_BACKOFF_BASE_SECONDS", 0.0
    )

    await live_engines._restart_engine("wf1")

    entry = live_engines._engines["wf1"]
    assert entry["status"] == "warmup"
    assert entry["restart_count"] == 1
    assert entry["error"] is None
    assert started == [entry]
