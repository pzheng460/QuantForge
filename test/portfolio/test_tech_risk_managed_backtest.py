from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from quantforge.portfolio.strategy_backtest import (
    TechRiskManagedConfig,
    run_tech_risk_managed_backtest,
)


def _market(days: int = 700) -> dict[str, list[list]]:
    start = date(2020, 1, 1)
    prices = {"XLK": 100.0, "SMH": 100.0, "TSLA": 100.0, "NVDA": 100.0}
    result = {symbol: [] for symbol in prices}
    trading_day = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        trading_day += 1
        for symbol in prices:
            change = 0.001
            if 330 <= trading_day < 345:
                change = -0.04
            prices[symbol] *= 1 + change
            timestamp = int(
                datetime.combine(
                    day,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ).timestamp()
                * 1000
            )
            close = prices[symbol]
            result[symbol].append(
                [timestamp, close, close * 1.01, close * 0.99, close, 1000]
            )
    return result


def test_tech_portfolio_respects_caps_and_enters_cash_cooldown():
    result = run_tech_risk_managed_backtest(
        _market(),
        initial_capital=100_000,
        config=TechRiskManagedConfig(),
    )

    assert result.quality == "default_unvalidated"
    assert result.maximum_target_gross_exposure <= 0.95 + 1e-9
    assert result.max_target_weights["XLK"] <= 0.60 + 1e-9
    assert result.max_target_weights["SMH"] <= 0.25 + 1e-9
    assert result.max_target_weights["TSLA"] <= 0.05 + 1e-9
    assert result.max_target_weights["NVDA"] <= 0.05 + 1e-9
    assert result.action_counts["hard_stop"] >= 1
    assert result.cash_cooldown_days >= 20
    assert result.action_counts["soft_deleverage"] < 20


def test_costs_cannot_improve_tech_portfolio_equity():
    market = _market()
    free = run_tech_risk_managed_backtest(
        market,
        initial_capital=100_000,
        config=TechRiskManagedConfig(
            commission_bps=0,
            slippage_bps=0,
        ),
    )
    costly = run_tech_risk_managed_backtest(
        market,
        initial_capital=100_000,
        config=TechRiskManagedConfig(
            commission_bps=1,
            slippage_bps=2,
        ),
    )

    assert costly.total_costs > 0
    assert costly.final_equity < free.final_equity
