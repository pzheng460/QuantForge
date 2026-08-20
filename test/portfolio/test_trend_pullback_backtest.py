from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from quantforge.portfolio.trend_pullback import (
    TrendPullbackConfig,
    run_trend_pullback_backtest,
)


def _bars(days: int = 900) -> list[list[float]]:
    start = date(2020, 1, 1)
    price = 100.0
    rows: list[list[float]] = []
    trading_day = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        trading_day += 1
        if trading_day < 250:
            change = 0.0002 + (0.001 if trading_day % 2 else -0.001)
        elif trading_day < 430:
            change = 0.003 + (0.002 if trading_day % 2 else -0.002)
        elif trading_day < 450:
            change = -0.004
        elif trading_day < 560:
            change = 0.003 + (0.002 if trading_day % 2 else -0.002)
        else:
            change = -0.008
        open_price = price
        price *= 1 + change
        timestamp = int(
            datetime.combine(
                day,
                datetime.min.time(),
                tzinfo=timezone.utc,
            ).timestamp()
            * 1000
        )
        rows.append(
            [
                timestamp,
                open_price,
                max(open_price, price) * 1.002,
                min(open_price, price) * 0.998,
                price,
                1_000,
            ]
        )
    return rows


def test_trend_pullback_enters_uptrend_and_exits_breakdown():
    result = run_trend_pullback_backtest(
        _bars(),
        initial_capital=100_000,
        config=TrendPullbackConfig(),
    )

    assert result.quality == "default_unvalidated"
    assert result.trade_count >= 1
    assert result.maximum_target_exposure <= 1.0
    assert result.time_in_market_pct > 0
    assert result.action_counts["trend_entry"] >= 1
    assert result.action_counts["exit"] >= 1


def test_trading_costs_cannot_improve_final_equity():
    bars = _bars()
    free = run_trend_pullback_backtest(
        bars,
        initial_capital=100_000,
        config=TrendPullbackConfig(commission_bps=0, slippage_bps=0),
    )
    costly = run_trend_pullback_backtest(
        bars,
        initial_capital=100_000,
        config=TrendPullbackConfig(commission_bps=1, slippage_bps=2),
    )

    assert costly.total_costs > 0
    assert costly.final_equity < free.final_equity
