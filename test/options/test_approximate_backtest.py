from __future__ import annotations

from quantforge.options.backtest import run_covered_call_approximation


def test_option_backtest_is_explicitly_approximate_and_deterministic():
    bars = []
    for i in range(100):
        close = 100 + i * 0.2
        bars.append([i * 86_400_000, close, close + 1, close - 1, close, 1000])

    first = run_covered_call_approximation(bars, initial_capital=100_000)
    second = run_covered_call_approximation(bars, initial_capital=100_000)

    assert first.quality == "approximate_unvalidated"
    assert first.result.equity_curve == second.result.equity_curve
    assert len(first.result.equity_curve) == len(bars)
    assert first.result.trades
