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


@pytest.mark.asyncio
async def test_start_engine_rejects_duplicate_atomically(monkeypatch):
    """Two start attempts for the same strategy: the second raises
    EngineAlreadyRunningError (mapped to 409 by the router). The check lives
    inside start_engine under the registry lock, so racing the router's
    friendly pre-check can never double-start an engine."""

    class _FakeEngine:
        _warmup_complete = True

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    def _fake_build(**kwargs):  # _build_runtime is a sync factory
        return _FakeEngine()

    monkeypatch.setattr(live_engines, "_build_runtime", _fake_build)
    monkeypatch.setattr(live_engines, "_start_task", lambda entry: None)
    monkeypatch.setattr(live_engines, "_single_instance", True)

    kwargs = dict(
        strategy="ema_crossover",
        exchange="okx",
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        demo=True,
        position_size_usdt=100.0,
        leverage=1,
        warmup_bars=50,
        config_override=None,
        risk_limits={},
    )
    first_id = await live_engines.start_engine(**kwargs)
    second_id = None
    try:
        with pytest.raises(live_engines.EngineAlreadyRunningError):
            await live_engines.start_engine(**kwargs)
        # A different strategy is still allowed.
        second_id = await live_engines.start_engine(
            **{**kwargs, "strategy": "flip_strategy"}
        )
    finally:
        for eid, entry in list(live_engines._engines.items()):
            await entry["engine"].stop()
            del live_engines._engines[eid]
    assert first_id and second_id and second_id != first_id
