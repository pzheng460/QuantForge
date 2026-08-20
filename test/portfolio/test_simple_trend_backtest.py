from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from quantforge.portfolio.simple_trend import (
    SimpleTrendConfig,
    run_simple_trend_backtest,
)


def _bars(days: int = 1_100) -> list[list[float]]:
    start = date(2020, 1, 1)
    price = 100.0
    result: list[list[float]] = []
    trading_day = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        trading_day += 1
        if trading_day < 320:
            change = 0.001
        elif trading_day < 480:
            change = -0.006
        else:
            change = 0.003
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
        result.append(
            [
                timestamp,
                open_price,
                max(open_price, price),
                min(open_price, price),
                price,
                1_000,
            ]
        )
    return result


def test_core_satellite_never_drops_below_core_or_above_full_exposure():
    result = run_simple_trend_backtest(
        _bars(),
        initial_capital=100_000,
        config=SimpleTrendConfig(
            mode="price_ma",
            core_exposure=0.70,
            long_ma=200,
        ),
    )

    assert result.minimum_target_exposure >= 0.70
    assert result.maximum_target_exposure <= 1.0
    assert result.action_counts["risk_on"] >= 1
    assert result.action_counts["risk_off"] >= 1


def test_costs_cannot_improve_simple_trend_equity():
    bars = _bars()
    free = run_simple_trend_backtest(
        bars,
        initial_capital=100_000,
        config=SimpleTrendConfig(commission_bps=0, slippage_bps=0),
    )
    costly = run_simple_trend_backtest(
        bars,
        initial_capital=100_000,
        config=SimpleTrendConfig(commission_bps=1, slippage_bps=2),
    )

    assert costly.total_costs > 0
    assert costly.final_equity < free.final_equity
