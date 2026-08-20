"""CPU-bound backtest/optimize jobs share one concurrency cap.

Without it, a burst of legal parallel jobs would saturate asyncio's default
executor and starve the event loop. The semaphore caps simultaneous
executions; excess jobs queue instead of failing.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from apps.dashboard.backend.jobs import concurrency
from apps.dashboard.backend.jobs import backtest as bt
from apps.dashboard.backend.jobs import optimize as opt
from apps.dashboard.backend.models import BacktestRequest, OptimizeRequest


def _dummy_result() -> dict:
    return {
        "trades": [],
        "equity_curve": [100_000.0],
        "initial_capital": 100_000.0,
        "final_equity": 100_000.0,
        "total_return_pct": 0.0,
        "sharpe": 0.0,
        "max_drawdown_pct": 0.0,
    }


@pytest.mark.asyncio
async def test_backtest_concurrency_is_capped(monkeypatch):
    concurrency.replace_semaphore(2)
    try:
        active = 0
        peak = 0

        def slow_backtest(req, job_id):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.02)
            active -= 1
            return _dummy_result()

        monkeypatch.setattr(bt, "_run_python_backtest", slow_backtest)
        req = BacktestRequest(
            strategy="ema_cross",
            symbol="BTC/USDT",
            timeframe="1h",
            exchange="okx",
            position_size_usdt=100_000,
        )
        await asyncio.gather(
            *(bt.run_backtest_job(f"bt-{i}", req) for i in range(6))
        )
        assert peak <= 2
        assert peak >= 1
    finally:
        concurrency.replace_semaphore(concurrency.MAX_PARALLEL_JOBS)


@pytest.mark.asyncio
async def test_optimize_concurrency_is_capped(monkeypatch):
    concurrency.replace_semaphore(1)
    try:
        active = 0
        peak = 0

        def slow_grid(req, job_id):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.02)
            active -= 1
            return {"rows": [], "best": None, "count": 0}

        monkeypatch.setattr(opt, "_run_python_optimize", slow_grid)
        req = OptimizeRequest(
            strategy="ema_cross",
            symbol="BTC/USDT",
            timeframe="1h",
            exchange="okx",
            mode="grid",
            param_grid={},
        )
        await asyncio.gather(
            *(opt.run_optimize_job(f"opt-{i}", req) for i in range(4))
        )
        assert peak == 1
    finally:
        concurrency.replace_semaphore(concurrency.MAX_PARALLEL_JOBS)


@pytest.mark.asyncio
async def test_release_happens_even_when_job_raises(monkeypatch):
    concurrency.replace_semaphore(1)
    try:
        def explode(req, job_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(bt, "_run_python_backtest", explode)
        req = BacktestRequest(
            strategy="ema_cross",
            symbol="BTC/USDT",
            timeframe="1h",
            exchange="okx",
        )
        await bt.run_backtest_job("bt-fail", req)
        # The failed job must have released its slot: another job can run.
        done = asyncio.Event()

        def slow(req, job_id):
            done.set()
            return _dummy_result()

        monkeypatch.setattr(bt, "_run_python_backtest", slow)
        await bt.run_backtest_job("bt-ok", req)
        assert done.is_set()
    finally:
        concurrency.replace_semaphore(concurrency.MAX_PARALLEL_JOBS)
