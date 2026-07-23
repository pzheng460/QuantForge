from __future__ import annotations

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
