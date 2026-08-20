"""Cancellation must take effect inside a running job, not after it returns.

Regression tests for the review finding that backtest jobs were wrapped in a
single ``asyncio.to_thread`` with ``check_cancelled`` only called AFTER the
thread finished (so a long backtest was uninterruptible), and that the WFO /
three-stage optimize modes never propagated ``job_id`` at all.
"""

from __future__ import annotations

import pytest

import apps.dashboard.backend.jobs.registry as registry
from apps.dashboard.backend.jobs import backtest as backtest_jobs
from apps.dashboard.backend.jobs import optimize as optimize_jobs
from apps.dashboard.backend.jobs.registry import (
    JobCancelled,
    cancel_job,
    check_cancelled,
    create_job,
)
from apps.dashboard.backend.models import BacktestRequest, OptimizeRequest


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_PERSIST_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(registry, "_jobs", {})
    monkeypatch.setattr(registry, "_cancel_flags", {})
    monkeypatch.setattr(registry, "_backend", registry._FileBackend())
    registry._load_jobs()
    yield registry


def _bars(n=200, day=86_400_000):
    start = 1_767_225_600_000  # 2026-01-01
    return [
        [start + i * day, 100 + i % 7, 101 + i % 7, 99 + i % 7, 100 + i % 7, 1000]
        for i in range(n)
    ]


def _backtest_req() -> BacktestRequest:
    return BacktestRequest(
        strategy="ema_crossover",
        exchange="bitget",
        timeframe="1d",
        start_date="2026-01-01",
        end_date="2026-02-28",
        warmup_bars=0,
        config_override={"fast_period": 3, "slow_period": 10},
    )


def _optimize_req(mode: str) -> OptimizeRequest:
    return OptimizeRequest(
        strategy="ema_crossover",
        exchange="bitget",
        timeframe="1d",
        period="1y",
        mode=mode,
        warmup_bars=0,
    )


def test_backtest_job_checks_cancel_during_run(isolated_registry, monkeypatch):
    """The cancel check must live inside the computation: with a pre-set
    cancel flag, JobCancelled surfaces from _run_python_backtest (not from a
    post-completion check in the async wrapper)."""
    seen: list[str] = []
    real = check_cancelled

    def spy(job_id: str) -> None:
        seen.append(job_id)
        real(job_id)

    monkeypatch.setattr(backtest_jobs, "check_cancelled", spy)
    monkeypatch.setattr(backtest_jobs, "_fetch_ohlcv", lambda *a, **k: _bars())

    job_id = create_job()
    cancel_job(job_id)
    with pytest.raises(JobCancelled):
        backtest_jobs._run_python_backtest(_backtest_req(), job_id)
    assert job_id in seen


@pytest.mark.asyncio
async def test_backtest_job_end_to_end_cancel(isolated_registry, monkeypatch):
    """JobCancelled crosses the asyncio.to_thread boundary and lands as a
    'cancelled' registry status."""
    monkeypatch.setattr(backtest_jobs, "_fetch_ohlcv", lambda *a, **k: _bars())

    job_id = create_job()
    cancel_job(job_id)
    await backtest_jobs.run_backtest_job(job_id, _backtest_req())
    assert registry.get_job(job_id)["status"] == "cancelled"


@pytest.mark.parametrize("mode", ["wfo", "full"])
def test_optimize_modes_check_cancel_during_run(
    isolated_registry, monkeypatch, mode
):
    """WFO and three-stage modes must accept job_id and honor it; previously
    they never received job_id so a cancel could not interrupt them."""
    seen: list[str] = []
    real = check_cancelled

    def spy(job_id: str) -> None:
        seen.append(job_id)
        real(job_id)

    monkeypatch.setattr(optimize_jobs, "check_cancelled", spy)
    monkeypatch.setattr(optimize_jobs, "_fetch_ohlcv", lambda *a, **k: _bars(400))

    job_id = create_job()
    req = _optimize_req(mode)
    cancel_job(job_id)
    with pytest.raises(JobCancelled):
        if mode == "wfo":
            optimize_jobs._run_wfo(req, job_id)
        else:
            optimize_jobs._run_three_stage(req, job_id)
    assert job_id in seen
