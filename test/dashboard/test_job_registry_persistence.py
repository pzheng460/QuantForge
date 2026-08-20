"""Job-registry durability: completed pydantic results must survive a restart.

Regression test for the finding that ``_json_default`` only handled datetimes,
so every completed backtest result made ``json.dumps`` raise TypeError which
was silently swallowed — the registry never actually persisted anything real.
"""

from __future__ import annotations

import json

import pytest

from apps.dashboard.backend.models import BacktestResultOut


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    import apps.dashboard.backend.jobs.registry as registry

    monkeypatch.setattr(registry, "_PERSIST_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(registry, "_jobs", {})
    monkeypatch.setattr(registry, "_cancel_flags", {})
    monkeypatch.setattr(registry, "_backend", registry._FileBackend())
    registry._load_jobs()
    yield registry


def _result() -> BacktestResultOut:
    return BacktestResultOut(
        strategy="ema_crossover",
        exchange="okx",
        period_start="2025-01-01",
        period_end="2025-06-01",
        config_name="ema_crossover",
        bh_return_pct=2.0,
        max_consecutive_wins=3,
        max_consecutive_losses=1,
        avg_trade_duration_hours=6.0,
        total_return_pct=1.0,
        max_drawdown_pct=-2.0,
        total_trades=3,
        win_rate_pct=33.0,
        profit_factor=1.1,
        payoff_ratio=1.0,
        avg_win=100.0,
        avg_loss=-90.0,
        expectancy=3.0,
        largest_win=200.0,
        largest_loss=-150.0,
        sharpe_ratio=0.8,
        sortino_ratio=0.9,
        calmar_ratio=0.5,
        annualized_return_pct=4.0,
        annualized_volatility_pct=10.0,
        recovery_factor=1.5,
        max_dd_duration_days=5,
        final_equity=101000.0,
        equity_curve=[{"t": "2025-01-01T00:00:00+00:00", "strategy": 101000.0, "bh": 102000.0}],
        drawdown_curve=[],
        monthly_returns=[],
        trades=[],
    )


def test_completed_job_with_pydantic_result_persists_and_round_trips(isolated_registry):
    job_id = isolated_registry.create_job()
    isolated_registry.update_job(job_id, result=_result(), status="completed")

    # The file must actually contain the result on disk.
    payload = json.loads(isolated_registry._PERSIST_PATH.read_text(encoding="utf-8"))
    assert payload[job_id]["status"] == "completed"
    assert payload[job_id]["result"]["strategy"] == "ema_crossover"
    assert payload[job_id]["result"]["final_equity"] == 101000.0

    # Simulate a process restart: drop memory and reload from the same file.
    isolated_registry._jobs.clear()
    isolated_registry._load_jobs()
    restored = isolated_registry.get_job(job_id)
    assert restored is not None
    assert restored["status"] == "completed"
    assert restored["result"]["final_equity"] == 101000.0


def test_failed_job_error_persists(isolated_registry):
    job_id = isolated_registry.create_job()
    isolated_registry.update_job(job_id, status="failed", error="boom")
    isolated_registry._jobs.clear()
    isolated_registry._load_jobs()
    restored = isolated_registry.get_job(job_id)
    assert restored["status"] == "failed"
    assert restored["error"] == "boom"


def test_created_at_survives_round_trip_as_datetime(isolated_registry):
    job_id = isolated_registry.create_job()
    isolated_registry._jobs.clear()
    isolated_registry._load_jobs()
    from datetime import datetime

    assert isinstance(isolated_registry.get_job(job_id)["created_at"], datetime)
