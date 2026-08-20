"""DEPRECATED legacy standalone procedural backtest (daily US-equity klines).

Kept for reference and existing tests only. It is NOT part of the canonical
Strategy → RiskEngine → ExecutionService path and does not share the strategy
implementation used by live trading. New work must use quantforge.strategy
strategies with the shared backtest (quantforge.backtest) and live
(quantforge.live) engines instead.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np


@dataclass(frozen=True, slots=True)
class SimpleTrendConfig:
    mode: str = "price_ma"
    long_ma: int = 200
    fast_ma: int = 50
    hysteresis_pct: float = 0.02
    core_exposure: float = 0.70
    commission_bps: float = 1.0
    slippage_bps: float = 2.0


@dataclass(frozen=True, slots=True)
class SimpleTrendBacktest:
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
    buy_hold_cagr_pct: float
    buy_hold_max_drawdown_pct: float
    trade_count: int
    time_at_full_exposure_pct: float
    annual_turnover: float
    total_costs: float
    minimum_target_exposure: float
    maximum_target_exposure: float
    action_counts: dict[str, int]
    equity_curve: tuple[float, ...]


def _day(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()


def _validate(config: SimpleTrendConfig) -> None:
    if config.mode not in {"price_ma", "ma_crossover"}:
        raise ValueError("mode must be price_ma or ma_crossover")
    if config.long_ma < 2:
        raise ValueError("long_ma must be at least two")
    if not 1 <= config.fast_ma < config.long_ma:
        raise ValueError("fast_ma must be shorter than long_ma")
    if not 0 <= config.hysteresis_pct < 0.25:
        raise ValueError("hysteresis_pct must be in [0, 0.25)")
    if not 0 <= config.core_exposure <= 1:
        raise ValueError("core_exposure must be in [0, 1]")


def _performance(
    curve: list[float],
    years: float,
) -> tuple[float, float, float, float]:
    values = np.asarray(curve, dtype=float)
    returns = values[1:] / values[:-1] - 1
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    volatility = float(np.std(returns, ddof=0) * math.sqrt(252))
    sharpe = (
        float(np.mean(returns) * 252 / volatility)
        if volatility > 1e-12
        else 0.0
    )
    peak = np.maximum.accumulate(values)
    drawdown = float(np.min(values / peak - 1))
    return cagr, volatility, sharpe, drawdown


def run_simple_trend_backtest(
    bars: list[list | tuple],
    *,
    initial_capital: float,
    config: SimpleTrendConfig | None = None,
    evaluation_start: date | None = None,
) -> SimpleTrendBacktest:
    """Backtest a price/MA or MA-crossover exposure overlay."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    cfg = config or SimpleTrendConfig()
    _validate(cfg)
    ordered = sorted(bars, key=lambda row: int(row[0]))
    if len(ordered) <= cfg.long_ma + 2:
        raise ValueError("insufficient bars for long moving average")
    dates = [_day(int(row[0])) for row in ordered]
    opens = np.asarray([float(row[1]) for row in ordered])
    closes = np.asarray([float(row[4]) for row in ordered])
    if np.any(opens <= 0) or np.any(closes <= 0):
        raise ValueError("prices must be positive")

    long_average = np.full(len(closes), np.nan)
    fast_average = np.full(len(closes), np.nan)
    for index in range(cfg.long_ma - 1, len(closes)):
        long_average[index] = np.mean(
            closes[index - cfg.long_ma + 1 : index + 1]
        )
        fast_average[index] = np.mean(
            closes[index - cfg.fast_ma + 1 : index + 1]
        )
    start = next(
        (
            index
            for index, day in enumerate(dates)
            if index >= cfg.long_ma
            and (evaluation_start is None or day >= evaluation_start)
        ),
        len(dates),
    )
    if start >= len(dates) - 1:
        raise ValueError("evaluation_start leaves no evaluable observations")

    def desired_state(index: int, current: bool | None) -> bool:
        if cfg.mode == "ma_crossover":
            return bool(fast_average[index] > long_average[index])
        upper = long_average[index] * (1 + cfg.hysteresis_pct)
        lower = long_average[index] * (1 - cfg.hysteresis_pct)
        if closes[index] > upper:
            return True
        if closes[index] < lower:
            return False
        return bool(closes[index] >= long_average[index]) if current is None else current

    risk_on = desired_state(start - 1, None)
    pending_weight = 1.0 if risk_on else cfg.core_exposure
    cash = initial_capital
    shares = 0.0
    turnover = 0.0
    total_costs = 0.0
    curve: list[float] = []
    target_history: list[float] = []
    full_days = 0
    actions: Counter[str] = Counter()

    def equity(price: float) -> float:
        return cash + shares * price

    def rebalance(weight: float, price: float) -> None:
        nonlocal cash, shares, turnover, total_costs
        current_equity = equity(price)
        desired_shares = current_equity * weight / price
        traded = abs(desired_shares - shares) * price
        cost = traded * (cfg.commission_bps + cfg.slippage_bps) / 10_000
        cash -= (desired_shares - shares) * price + cost
        shares = desired_shares
        turnover += traded
        total_costs += cost

    for index in range(start, len(ordered)):
        if pending_weight is not None:
            rebalance(pending_weight, opens[index])
            target_history.append(pending_weight)
            actions["risk_on" if pending_weight >= 1 - 1e-12 else "risk_off"] += 1
            pending_weight = None
        curve.append(equity(closes[index]))
        if risk_on:
            full_days += 1
        if index + 1 >= len(ordered):
            continue
        next_state = desired_state(index, risk_on)
        if next_state != risk_on:
            risk_on = next_state
            pending_weight = 1.0 if risk_on else cfg.core_exposure

    years = max((dates[-1] - dates[start]).days / 365.25, 1 / 365.25)
    cagr, volatility, sharpe, max_drawdown = _performance(curve, years)
    buy_hold_curve = closes[start:] / opens[start]
    buy_hold_cagr = buy_hold_curve[-1] ** (1 / years) - 1
    buy_hold_peak = np.maximum.accumulate(buy_hold_curve)
    buy_hold_drawdown = float(np.min(buy_hold_curve / buy_hold_peak - 1))
    return SimpleTrendBacktest(
        quality="default_unvalidated",
        start_date=dates[start].isoformat(),
        end_date=dates[-1].isoformat(),
        initial_capital=initial_capital,
        final_equity=curve[-1],
        total_return_pct=(curve[-1] / initial_capital - 1) * 100,
        cagr_pct=cagr * 100,
        annualized_volatility_pct=volatility * 100,
        max_drawdown_pct=max_drawdown * 100,
        sharpe=sharpe,
        buy_hold_cagr_pct=buy_hold_cagr * 100,
        buy_hold_max_drawdown_pct=buy_hold_drawdown * 100,
        trade_count=len(target_history),
        time_at_full_exposure_pct=full_days / len(curve) * 100,
        annual_turnover=turnover / initial_capital / years,
        total_costs=total_costs,
        minimum_target_exposure=min(target_history),
        maximum_target_exposure=max(target_history),
        action_counts=dict(sorted(actions.items())),
        equity_curve=tuple(curve),
    )
