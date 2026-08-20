"""DEPRECATED legacy standalone procedural backtest (daily US-equity klines).

Kept for reference and existing tests only. It is NOT part of the canonical
Strategy → RiskEngine → ExecutionService path and does not share the strategy
implementation used by live trading. New work must use quantforge.strategy
strategies with the shared backtest (quantforge.backtest) and live
(quantforge.live) engines instead.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np


SYMBOLS = ("XLK", "SMH", "TSLA", "NVDA")


@dataclass(frozen=True, slots=True)
class TechRiskManagedConfig:
    base_weights: tuple[tuple[str, float], ...] = (
        ("XLK", 0.60),
        ("SMH", 0.25),
        ("TSLA", 0.05),
        ("NVDA", 0.05),
    )
    target_volatility: float = 0.12
    maximum_gross_exposure: float = 0.95
    minimum_risk_exposure: float = 0.30
    soft_drawdown: float = 0.08
    hard_drawdown: float = 0.12
    soft_risk_multiplier: float = 0.50
    cooldown_days: int = 20
    commission_bps: float = 1.0
    slippage_bps: float = 2.0


@dataclass(frozen=True, slots=True)
class TechRiskManagedBacktest:
    quality: str
    start_date: str
    end_date: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    annualized_volatility_pct: float
    max_drawdown_pct: float
    sharpe: float
    xlk_buy_hold_cagr_pct: float
    static_portfolio_cagr_pct: float
    total_costs: float
    annual_turnover: float
    average_cash_pct: float
    cash_cooldown_days: int
    maximum_target_gross_exposure: float
    max_target_weights: dict[str, float]
    action_counts: dict[str, int]
    equity_curve: tuple[float, ...]


def _day(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).date()


def _align(
    markets: dict[str, list[list | tuple]],
) -> tuple[list[date], dict[str, list[list | tuple]]]:
    if any(symbol not in markets for symbol in SYMBOLS):
        raise ValueError(f"markets must include {', '.join(SYMBOLS)}")
    indexed = {
        symbol: {_day(int(row[0])): row for row in markets[symbol]}
        for symbol in SYMBOLS
    }
    common = sorted(set.intersection(*(set(rows) for rows in indexed.values())))
    if len(common) < 253:
        raise ValueError("at least 253 aligned daily bars are required")
    return common, {
        symbol: [indexed[symbol][day] for day in common]
        for symbol in SYMBOLS
    }


def _returns(closes: list[float]) -> np.ndarray:
    values = np.asarray(closes, dtype=float)
    return values[1:] / values[:-1] - 1


def _rolling_volatility(closes: list[float], end: int) -> float:
    returns = _returns(closes[max(0, end - 20) : end + 1])
    return float(np.std(returns, ddof=0) * math.sqrt(252))


def _eligible(closes: list[float], end: int) -> bool:
    if end < 252:
        return False
    current = closes[end]
    ma50 = statistics.fmean(closes[end - 49 : end + 1])
    ma200 = statistics.fmean(closes[end - 199 : end + 1])
    current_volatility = _rolling_volatility(closes, end)
    volatility_history = [
        _rolling_volatility(closes, index)
        for index in range(max(20, end - 251), end + 1)
    ]
    threshold = float(np.quantile(volatility_history, 0.80))
    return (
        current > ma200
        and ma50 > ma200
        and current_volatility <= threshold
    )


def _target_weights(
    closes: dict[str, list[float]],
    end: int,
    config: TechRiskManagedConfig,
    *,
    risk_multiplier: float,
) -> dict[str, float]:
    base = dict(config.base_weights)
    eligible = {
        symbol: _eligible(closes[symbol], end)
        for symbol in SYMBOLS
    }
    raw = np.asarray(
        [base[symbol] if eligible[symbol] else 0.0 for symbol in SYMBOLS],
        dtype=float,
    )
    if raw.sum() == 0:
        return {symbol: 0.0 for symbol in SYMBOLS}
    matrix = np.column_stack(
        [
            _returns(closes[symbol][end - 20 : end + 1])
            for symbol in SYMBOLS
        ]
    )
    covariance = np.cov(matrix, rowvar=False, ddof=0) * 252
    portfolio_volatility = float(math.sqrt(max(0.0, raw @ covariance @ raw)))
    scale = (
        config.target_volatility / portfolio_volatility
        if portfolio_volatility > 1e-9
        else 1.0
    )
    target_gross = raw.sum() * scale
    target_gross = min(config.maximum_gross_exposure, target_gross)
    target_gross = max(
        min(config.minimum_risk_exposure, raw.sum()),
        target_gross,
    )
    weights = raw / raw.sum() * target_gross * risk_multiplier
    capped = np.asarray(
        [min(weights[index], base[symbol]) for index, symbol in enumerate(SYMBOLS)]
    )
    if capped.sum() > config.maximum_gross_exposure:
        capped *= config.maximum_gross_exposure / capped.sum()
    return {
        symbol: float(capped[index])
        for index, symbol in enumerate(SYMBOLS)
    }


def _performance(
    curve: list[float],
    years: float,
) -> tuple[float, float, float, float]:
    returns = np.asarray(curve[1:], dtype=float) / np.asarray(
        curve[:-1],
        dtype=float,
    ) - 1
    cagr = (curve[-1] / curve[0]) ** (1 / years) - 1
    volatility = float(np.std(returns, ddof=0) * math.sqrt(252))
    sharpe = (
        float(np.mean(returns) * 252 / volatility)
        if volatility > 1e-9
        else 0.0
    )
    peak = curve[0]
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return cagr, volatility, sharpe, drawdown


def run_tech_risk_managed_backtest(
    markets: dict[str, list[list | tuple]],
    *,
    initial_capital: float,
    config: TechRiskManagedConfig | None = None,
    evaluation_start: date | None = None,
) -> TechRiskManagedBacktest:
    """Backtest the approved technology allocation with next-open execution."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    cfg = config or TechRiskManagedConfig()
    dates, rows = _align(markets)
    closes = {
        symbol: [float(row[4]) for row in rows[symbol]]
        for symbol in SYMBOLS
    }
    start_index = next(
        (
            index
            for index, day in enumerate(dates)
            if index >= 252
            and (evaluation_start is None or day >= evaluation_start)
        ),
        len(dates),
    )
    if start_index >= len(dates):
        raise ValueError("evaluation_start leaves no evaluable observations")

    cash = initial_capital
    quantities = {symbol: 0.0 for symbol in SYMBOLS}
    curve: list[float] = []
    total_costs = 0.0
    turnover = 0.0
    cash_weights: list[float] = []
    max_target_weights = {symbol: 0.0 for symbol in SYMBOLS}
    maximum_target_gross = 0.0
    actions: Counter[str] = Counter()
    cooldown_remaining = 0
    cash_cooldown_days = 0
    control_peak = initial_capital
    pending_emergency: str | None = None
    soft_deleverage_active = False
    last_month: tuple[int, int] | None = None

    def value(prices: dict[str, float]) -> float:
        return cash + sum(
            quantities[symbol] * prices[symbol]
            for symbol in SYMBOLS
        )

    def rebalance(
        weights: dict[str, float],
        prices: dict[str, float],
    ) -> None:
        nonlocal cash, total_costs, turnover, maximum_target_gross
        maximum_target_gross = max(
            maximum_target_gross,
            sum(abs(weight) for weight in weights.values()),
        )
        for symbol, weight in weights.items():
            max_target_weights[symbol] = max(
                max_target_weights[symbol],
                weight,
            )
        equity = value(prices)
        desired = {
            symbol: equity * weights[symbol] / prices[symbol]
            for symbol in SYMBOLS
        }
        traded = sum(
            abs(desired[symbol] - quantities[symbol]) * prices[symbol]
            for symbol in SYMBOLS
        )
        cost = traded * (cfg.commission_bps + cfg.slippage_bps) / 10_000
        for symbol in SYMBOLS:
            cash -= (desired[symbol] - quantities[symbol]) * prices[symbol]
            quantities[symbol] = desired[symbol]
        cash -= cost
        total_costs += cost
        turnover += traded

    for index in range(start_index, len(dates)):
        opens = {
            symbol: float(rows[symbol][index][1])
            for symbol in SYMBOLS
        }
        month = (dates[index].year, dates[index].month)
        month_changed = month != last_month
        if month_changed:
            last_month = month

        if pending_emergency == "hard":
            rebalance({symbol: 0.0 for symbol in SYMBOLS}, opens)
            cooldown_remaining = cfg.cooldown_days
            soft_deleverage_active = False
            actions["hard_stop"] += 1
            pending_emergency = None
        elif cooldown_remaining > 0:
            if any(abs(quantity) > 1e-12 for quantity in quantities.values()):
                rebalance({symbol: 0.0 for symbol in SYMBOLS}, opens)
            cash_cooldown_days += 1
            cooldown_remaining -= 1
            if cooldown_remaining == 0:
                control_peak = value(opens)
                actions["cooldown_complete"] += 1
        elif pending_emergency == "soft":
            current_equity = value(opens)
            current_weights = {
                symbol: quantities[symbol] * opens[symbol] / current_equity
                for symbol in SYMBOLS
            }
            rebalance(
                {
                    symbol: weight * cfg.soft_risk_multiplier
                    for symbol, weight in current_weights.items()
                },
                opens,
            )
            actions["soft_deleverage"] += 1
            soft_deleverage_active = True
            pending_emergency = None
        elif month_changed:
            risk_drawdown = value(opens) / control_peak - 1
            multiplier = (
                cfg.soft_risk_multiplier
                if risk_drawdown <= -cfg.soft_drawdown
                else 1.0
            )
            target = _target_weights(
                closes,
                index - 1,
                cfg,
                risk_multiplier=multiplier,
            )
            rebalance(target, opens)
            actions["monthly_rebalance"] += 1

        close_prices = {
            symbol: closes[symbol][index]
            for symbol in SYMBOLS
        }
        equity = value(close_prices)
        curve.append(equity)
        control_peak = max(control_peak, equity)
        drawdown = equity / control_peak - 1
        if soft_deleverage_active and drawdown > -cfg.soft_drawdown / 2:
            soft_deleverage_active = False
            actions["soft_reset"] += 1
        if cooldown_remaining == 0:
            if drawdown <= -cfg.hard_drawdown:
                pending_emergency = "hard"
            elif (
                drawdown <= -cfg.soft_drawdown
                and pending_emergency is None
                and not soft_deleverage_active
            ):
                pending_emergency = "soft"

        cash_weights.append(max(0.0, cash / equity))

    years = max((dates[-1] - dates[start_index]).days / 365.25, 1 / 365.25)
    cagr, volatility, sharpe, max_drawdown = _performance(curve, years)
    start_prices = {
        symbol: closes[symbol][start_index]
        for symbol in SYMBOLS
    }
    end_prices = {symbol: closes[symbol][-1] for symbol in SYMBOLS}
    xlk_cagr = (
        end_prices["XLK"] / start_prices["XLK"]
    ) ** (1 / years) - 1
    base = dict(cfg.base_weights)
    static_return = sum(
        base[symbol] * end_prices[symbol] / start_prices[symbol]
        for symbol in SYMBOLS
    ) + (1 - sum(base.values()))
    static_cagr = static_return ** (1 / years) - 1
    return TechRiskManagedBacktest(
        quality="default_unvalidated",
        start_date=dates[start_index].isoformat(),
        end_date=dates[-1].isoformat(),
        initial_capital=initial_capital,
        final_equity=curve[-1],
        total_return_pct=(curve[-1] / initial_capital - 1) * 100,
        cagr_pct=cagr * 100,
        annualized_volatility_pct=volatility * 100,
        max_drawdown_pct=max_drawdown * 100,
        sharpe=sharpe,
        xlk_buy_hold_cagr_pct=xlk_cagr * 100,
        static_portfolio_cagr_pct=static_cagr * 100,
        total_costs=total_costs,
        annual_turnover=turnover / initial_capital / years,
        average_cash_pct=statistics.fmean(cash_weights) * 100,
        cash_cooldown_days=cash_cooldown_days,
        maximum_target_gross_exposure=maximum_target_gross,
        max_target_weights=max_target_weights,
        action_counts=dict(sorted(actions.items())),
        equity_curve=tuple(curve),
    )
