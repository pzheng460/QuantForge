from __future__ import annotations

import pytest

from apps.dashboard.backend.jobs import backtest as jobs
from apps.dashboard.backend.models import BacktestRequest


def _bars():
    day = 86_400_000
    closes = list(range(100, 130)) + list(range(130, 90, -1))
    start = 1_767_225_600_000
    return [
        [start + i * day, close, close + 1, close - 1, close, 1000]
        for i, close in enumerate(closes)
    ]


def test_python_backtest_returns_aligned_equity_drawdown_and_trades(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: _bars())

    result = jobs._run_python_backtest(
        BacktestRequest(
            strategy="ema_crossover",
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-03-12",
            warmup_bars=0,
            config_override={"fast_period": 3, "slow_period": 10},
        )
    )

    assert result.config_name.startswith("Python ")
    assert len(result.equity_curve) == len(result.drawdown_curve)
    assert result.equity_curve[0]["strategy"] == result.initial_capital
    assert result.total_trades > 0
    assert all(trade.entry_time <= trade.exit_time for trade in result.trades)


def test_python_backtest_has_no_future_data_dependency():
    import quantforge.strategies  # noqa: F401
    from quantforge.backtest import run_backtest
    from quantforge.strategy import get_strategy

    bars = _bars()
    prefix = run_backtest(
        get_strategy("ema_crossover"),
        bars[:40],
        strategy_config={"fast_period": 3, "slow_period": 10},
    )
    full = run_backtest(
        get_strategy("ema_crossover"),
        bars,
        strategy_config={"fast_period": 3, "slow_period": 10},
    )

    assert prefix.targets[:-1] == full.targets[:39]


def test_tsla_nvda_options_backtest_runs_managed_model(monkeypatch):
    """The dashboard options backtest must exercise the managed (live-mirror)
    covered-call model, not the hold-to-expiry approximation, and refuse
    non-TSLA/NVDA symbols instead of silently mislabeling the underlying."""
    day = 86_400_000
    start_ms = 1_767_225_600_000  # 2026-01-01
    # 420 warmup closes (>= 200 needed by the managed model) + 80 period days.
    start_warmup = start_ms - 420 * day
    rows = []
    price = 100.0
    for i in range(500):
        price += 0.05 if i % 2 else -0.05
        rows.append(
            [start_warmup + i * day, price, price + 1, price - 1, price, 1000]
        )
    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: rows)

    result = jobs._run_python_backtest(
        BacktestRequest(
            strategy="tsla_nvda_options",
            exchange="schwab",
            symbol="NVDA",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-03-21",
            warmup_bars=420,
            config_override={"coverage_ratio": 0.5, "profit_take": 0.7},
        )
    )

    assert result.data_quality == "approximate_unvalidated"
    assert len(result.equity_curve) == len(result.drawdown_curve)
    assert result.equity_curve[0]["strategy"] == result.initial_capital
    assert result.config_name.endswith("(approximate_unvalidated)")
    # The managed model charges option fees (the old approximation used fee=0)
    # and records realized trades through the shared ledger.
    assert result.total_trades > 0
    assert all(trade.fee > 0 for trade in result.trades)


def test_tsla_nvda_options_backtest_rejects_non_ticker_symbol(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: _bars())
    with pytest.raises(ValueError, match="requires symbol TSLA or NVDA"):
        jobs._run_python_backtest(
            BacktestRequest(
                strategy="tsla_nvda_options",
                exchange="schwab",
                symbol="BTC/USDT:USDT",
                timeframe="1d",
                start_date="2026-01-01",
                end_date="2026-03-01",
                warmup_bars=0,
            )
        )


def test_failed_backtest_job_persists_sanitized_error_only(monkeypatch):
    """The persisted job error must never contain raw exception text (which
    can embed paths or secrets); the client gets a stable category only."""
    import asyncio

    import apps.dashboard.backend.jobs.registry as registry
    from apps.dashboard.backend.http_errors import sanitize_exception
    from apps.dashboard.backend.jobs.backtest import run_backtest_job

    monkeypatch.setattr(registry, "_jobs", {})
    monkeypatch.setattr(
        jobs,
        "_run_python_backtest",
        lambda req, job_id=None: (_ for _ in ()).throw(
            RuntimeError("auth token=supersecret EXPIRED at /home/pzheng46/QuantForge")
        ),
    )
    job_id = registry.create_job()
    asyncio.run(
        run_backtest_job(
            job_id,
            BacktestRequest(
                strategy="ema_crossover",
                exchange="bitget",
                timeframe="1d",
                start_date="2026-01-01",
                end_date="2026-03-01",
                warmup_bars=0,
            ),
        )
    )

    assert registry._jobs[job_id]["status"] == "failed"
    error = registry._jobs[job_id]["error"]
    assert error == sanitize_exception(
        RuntimeError("x"), prefix="backtest job failed"
    )
    assert "supersecret" not in error
    assert "/home/pzheng46" not in error
    assert "RuntimeError" in error
