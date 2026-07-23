from __future__ import annotations

import pytest


EXPECTED_MIGRATED = {
    "bb_squeeze",
    "bb_squeeze_v2",
    "bollinger_band",
    "bollinger_band_v4",
    "dual_regime",
    "ema_crossover",
    "ema_crossover_v2",
    "ema_crossover_v3",
    "hurst_kalman",
    "macd_trend",
    "momentum_adx",
    "rsi_momentum",
    "sma_trend",
    "tsla_nvda_options",
}


@pytest.mark.critical
def test_all_builtin_strategies_are_registered_as_python():
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import list_strategies

    registered = {row["name"] for row in list_strategies()}
    assert EXPECTED_MIGRATED <= registered
    assert all(row["engine"] == "python" for row in list_strategies())


@pytest.mark.critical
def test_python_ema_strategy_runs_through_shared_backtester():
    import quantforge.strategies  # noqa: F401
    from quantforge.backtest.engine import BacktestConfig, run_backtest
    from quantforge.strategy import get_strategy

    bars = []
    closes = list(range(100, 130)) + list(range(130, 90, -1))
    for i, close in enumerate(closes):
        bars.append([i * 86_400_000, close, close + 1, close - 1, close, 1000])

    result = run_backtest(
        get_strategy("ema_crossover"),
        bars,
        strategy_config={"fast_period": 3, "slow_period": 10},
        config=BacktestConfig(initial_capital=100_000),
    )

    assert len(result.equity_curve) == len(bars)
    assert result.trades
    assert {trade.direction for trade in result.trades} <= {"long", "short"}
