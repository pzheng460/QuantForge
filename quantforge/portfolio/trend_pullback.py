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


@dataclass(frozen=True, slots=True)
class TrendPullbackConfig:
    fast_ma: int = 20
    slow_ma: int = 60
    slope_lookback: int = 20
    confirmation_days: int = 3
    trend_threshold_atr: float = 0.50
    pullback_distance_atr: float = 0.50
    target_volatility: float = 0.15
    maximum_exposure: float = 1.0
    initial_stop_atr: float = 2.0
    trailing_stop_atr: float = 3.0
    maximum_drawdown: float = 0.12
    commission_bps: float = 1.0
    slippage_bps: float = 2.0


@dataclass(frozen=True, slots=True)
class TrendPullbackBacktest:
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
    win_rate_pct: float
    average_holding_days: float
    time_in_market_pct: float
    annual_turnover: float
    total_costs: float
    maximum_target_exposure: float
    action_counts: dict[str, int]
    equity_curve: tuple[float, ...]


def _day(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()


def _validate(config: TrendPullbackConfig) -> None:
    if config.fast_ma >= config.slow_ma:
        raise ValueError("fast_ma must be shorter than slow_ma")
    if min(
        config.fast_ma,
        config.slope_lookback,
        config.confirmation_days,
    ) < 1:
        raise ValueError("lookbacks must be positive")
    if not 0 < config.target_volatility:
        raise ValueError("target_volatility must be positive")
    if not 0 < config.maximum_exposure <= 1:
        raise ValueError("maximum_exposure must be in (0, 1]")
    if not 0 < config.maximum_drawdown < 1:
        raise ValueError("maximum_drawdown must be in (0, 1)")


def _indicators(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    config: TrendPullbackConfig,
) -> dict[str, np.ndarray]:
    count = len(closes)
    fast = np.full(count, np.nan)
    slow = np.full(count, np.nan)
    atr = np.full(count, np.nan)
    volatility = np.full(count, np.nan)
    true_range = np.empty(count)
    true_range[0] = highs[0] - lows[0]
    true_range[1:] = np.maximum.reduce(
        (
            highs[1:] - lows[1:],
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        )
    )
    log_returns = np.diff(np.log(closes))
    for index in range(count):
        if index + 1 >= config.fast_ma:
            fast[index] = np.mean(
                closes[index - config.fast_ma + 1 : index + 1]
            )
        if index + 1 >= config.slow_ma:
            slow[index] = np.mean(
                closes[index - config.slow_ma + 1 : index + 1]
            )
        if index + 1 >= 20:
            atr[index] = np.mean(true_range[index - 19 : index + 1])
        if index >= 20:
            volatility[index] = (
                np.std(log_returns[index - 20 : index], ddof=0)
                * math.sqrt(252)
            )
    return {
        "fast": fast,
        "slow": slow,
        "atr": atr,
        "volatility": volatility,
    }


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


def run_trend_pullback_backtest(
    bars: list[list | tuple],
    *,
    initial_capital: float,
    config: TrendPullbackConfig | None = None,
    evaluation_start: date | None = None,
) -> TrendPullbackBacktest:
    """Run the frozen long-only trend-pullback rules with next-open execution."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    cfg = config or TrendPullbackConfig()
    _validate(cfg)
    ordered = sorted(bars, key=lambda row: int(row[0]))
    warmup = cfg.slow_ma + cfg.slope_lookback + cfg.confirmation_days
    if len(ordered) <= warmup + 1:
        raise ValueError("insufficient bars for strategy warmup")

    dates = [_day(int(row[0])) for row in ordered]
    opens = np.asarray([float(row[1]) for row in ordered])
    highs = np.asarray([float(row[2]) for row in ordered])
    lows = np.asarray([float(row[3]) for row in ordered])
    closes = np.asarray([float(row[4]) for row in ordered])
    if np.any(opens <= 0) or np.any(closes <= 0):
        raise ValueError("prices must be positive")
    indicator = _indicators(highs, lows, closes, cfg)

    start = next(
        (
            index
            for index, day in enumerate(dates)
            if index >= warmup
            and (evaluation_start is None or day >= evaluation_start)
        ),
        len(dates),
    )
    if start >= len(dates) - 1:
        raise ValueError("evaluation_start leaves no evaluable observations")

    cash = initial_capital
    shares = 0.0
    total_costs = 0.0
    turnover = 0.0
    maximum_target_exposure = 0.0
    pending: tuple[str, float] | None = None
    up_streak = 0
    down_streak = 0
    previous_confirmed_up = False
    entry_price = 0.0
    entry_equity = 0.0
    entry_index = 0
    entry_atr = 0.0
    highest_close = 0.0
    below_slow_streak = 0
    curve: list[float] = []
    invested_days = 0
    holding_days: list[int] = []
    trade_returns: list[float] = []
    actions: Counter[str] = Counter()
    portfolio_peak = initial_capital

    def equity(price: float) -> float:
        return cash + shares * price

    def target_exposure(index: int) -> float:
        realized = float(indicator["volatility"][index])
        if not math.isfinite(realized) or realized <= 1e-12:
            return 0.0
        return min(cfg.maximum_exposure, cfg.target_volatility / realized)

    def rebalance(weight: float, price: float) -> None:
        nonlocal cash, shares, total_costs, turnover
        current_equity = equity(price)
        desired_shares = current_equity * weight / price
        traded = abs(desired_shares - shares) * price
        cost = traded * (cfg.commission_bps + cfg.slippage_bps) / 10_000
        cash -= (desired_shares - shares) * price + cost
        shares = desired_shares
        turnover += traded
        total_costs += cost

    for index in range(start, len(ordered)):
        if pending is not None:
            action, weight = pending
            was_invested = shares > 1e-12
            rebalance(weight, opens[index])
            is_invested = shares > 1e-12
            if not was_invested and is_invested:
                entry_price = opens[index]
                entry_equity = equity(opens[index])
                entry_index = index
                entry_atr = float(indicator["atr"][index - 1])
                highest_close = closes[index - 1]
                actions[action] += 1
            elif was_invested and not is_invested:
                exit_equity = equity(opens[index])
                trade_returns.append(exit_equity / entry_equity - 1)
                holding_days.append(index - entry_index)
                actions["exit"] += 1
            elif was_invested and is_invested:
                actions[action] += 1
            pending = None

        close_equity = equity(closes[index])
        curve.append(close_equity)
        portfolio_peak = max(portfolio_peak, close_equity)
        if shares > 1e-12:
            invested_days += 1
            highest_close = max(highest_close, closes[index])

        fast = float(indicator["fast"][index])
        slow = float(indicator["slow"][index])
        atr = float(indicator["atr"][index])
        prior_slow = float(indicator["slow"][index - cfg.slope_lookback])
        usable = all(math.isfinite(value) for value in (fast, slow, atr, prior_slow))
        if not usable or atr <= 0:
            continue
        distance = (fast - slow) / atr
        slope = slow - prior_slow
        raw_up = (
            distance > cfg.trend_threshold_atr
            and slope > 0
            and closes[index] > slow
        )
        raw_down = (
            distance < -cfg.trend_threshold_atr
            and slope < 0
            and closes[index] < slow
        )
        up_streak = up_streak + 1 if raw_up else 0
        down_streak = down_streak + 1 if raw_down else 0
        confirmed_up = up_streak >= cfg.confirmation_days
        confirmed_down = down_streak >= cfg.confirmation_days

        if shares > 1e-12:
            below_slow_streak = (
                below_slow_streak + 1 if closes[index] < slow else 0
            )
            strategy_drawdown = close_equity / portfolio_peak - 1
            exit_signal = (
                below_slow_streak >= 2
                or confirmed_down
                or closes[index] <= entry_price - cfg.initial_stop_atr * entry_atr
                or closes[index] <= highest_close - cfg.trailing_stop_atr * atr
                or strategy_drawdown <= -cfg.maximum_drawdown
            )
            if exit_signal and index + 1 < len(ordered):
                pending = ("exit", 0.0)
            elif (
                confirmed_up
                and abs(closes[index] - fast)
                <= cfg.pullback_distance_atr * atr
                and closes[index] > slow
                and closes[index] > closes[index - 1]
                and index + 1 < len(ordered)
            ):
                target = target_exposure(index)
                current_weight = shares * closes[index] / close_equity
                if target > current_weight + 1e-6:
                    maximum_target_exposure = max(
                        maximum_target_exposure, target
                    )
                    pending = ("pullback_add", target)
        elif (
            confirmed_up
            and not previous_confirmed_up
            and index + 1 < len(ordered)
        ):
            target = target_exposure(index) * 0.5
            maximum_target_exposure = max(maximum_target_exposure, target)
            pending = ("trend_entry", target)

        previous_confirmed_up = confirmed_up

    if shares > 1e-12:
        final_equity = equity(closes[-1])
        trade_returns.append(final_equity / entry_equity - 1)
        holding_days.append(len(ordered) - 1 - entry_index)
    else:
        final_equity = equity(closes[-1])

    years = max((dates[-1] - dates[start]).days / 365.25, 1 / 365.25)
    cagr, volatility, sharpe, max_drawdown = _performance(curve, years)
    buy_hold_curve = closes[start:] / opens[start]
    buy_hold_years = years
    buy_hold_cagr = buy_hold_curve[-1] ** (1 / buy_hold_years) - 1
    buy_hold_peak = np.maximum.accumulate(buy_hold_curve)
    buy_hold_drawdown = float(np.min(buy_hold_curve / buy_hold_peak - 1))
    return TrendPullbackBacktest(
        quality="default_unvalidated",
        start_date=dates[start].isoformat(),
        end_date=dates[-1].isoformat(),
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return_pct=(final_equity / initial_capital - 1) * 100,
        cagr_pct=cagr * 100,
        annualized_volatility_pct=volatility * 100,
        max_drawdown_pct=max_drawdown * 100,
        sharpe=sharpe,
        buy_hold_cagr_pct=buy_hold_cagr * 100,
        buy_hold_max_drawdown_pct=buy_hold_drawdown * 100,
        trade_count=len(trade_returns),
        win_rate_pct=(
            sum(result > 0 for result in trade_returns)
            / len(trade_returns)
            * 100
            if trade_returns
            else 0.0
        ),
        average_holding_days=(
            statistics.fmean(holding_days) if holding_days else 0.0
        ),
        time_in_market_pct=invested_days / len(curve) * 100,
        annual_turnover=turnover / initial_capital / years,
        total_costs=total_costs,
        maximum_target_exposure=maximum_target_exposure,
        action_counts=dict(sorted(actions.items())),
        equity_curve=tuple(curve),
    )
