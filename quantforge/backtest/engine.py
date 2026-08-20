from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quantforge.strategy.bar import Bar, BarStrategy, PositionTarget


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_capital: float = 100_000
    commission_pct: float = 0
    slippage_pct: float = 0
    allocation_pct: float = 1
    #: Optional short-liquidation ratio. When > 0, a short position whose
    #: price has risen by this fraction from entry is force-closed at the
    #: current bar's close (a crude margin/liquidation model — without it
    #: short equity can go arbitrarily negative). 0 disables the model
    #: (backward-compatible default).
    short_liq_ratio: float = 0
    #: Optional hook invoked every ``cancel_check_every`` bars to abort a
    #: long run. It raises to propagate cancellation (the dashboard jobs use
    #: it to raise JobCancelled so /backtest/cancel stays effective on
    #: CPU-bound runs). If it raises, run_backtest stops immediately.
    cancel_check: Callable[[], None] | None = None
    cancel_check_every: int = 128


@dataclass(slots=True)
class BacktestTrade:
    direction: str
    entry_bar: int
    entry_price: float
    exit_bar: int
    exit_price: float
    quantity: float
    pnl: float
    fee: float
    mfe: float = 0
    mae: float = 0


@dataclass(slots=True)
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    targets: list[int] = field(default_factory=list)
    initial_capital: float = 100_000


def _normalize_bars(rows: list[list | tuple]) -> list[Bar]:
    bars = []
    for row in rows:
        if len(row) < 6:
            raise ValueError("bars must be [timestamp, open, high, low, close, volume]")
        open_, high, low, close, volume = (
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        )
        if (
            not math.isfinite(open_)
            or not math.isfinite(high)
            or not math.isfinite(low)
            or not math.isfinite(close)
            or not math.isfinite(volume)
            or open_ <= 0
            or low <= 0
            or high < low
        ):
            raise ValueError(
                "bars must have positive finite open/low and finite "
                "high/close/volume with high >= low"
            )
        bars.append(
            Bar(
                timestamp=int(row[0]),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return bars


def run_backtest(
    strategy_cls: type[BarStrategy],
    bars: list[list | tuple],
    *,
    strategy_config: dict[str, Any] | None = None,
    config: BacktestConfig | None = None,
    warmup_bars: int = 0,
) -> BacktestResult:
    """Run reviewed Python strategy code with next-bar-open order semantics.

    Decisions are made on bar ``i``'s close/high/low and fills happen at bar
    ``i+1``'s open (market-on-open). This is a DOCUMENTED semantic, shared
    with the live engine (which decides on the current bar's close and submits
    for the following bar) — it is deliberately NOT same-bar-close fills, which
    would claim a fill that was never guaranteed.

    Fidelity note: this engine approximates the live next-bar-open fill
    semantics; it does NOT exercise the canonical live stack (RiskEngine /
    PortfolioLedger / ExecutionService). Risk behavior therefore differs from
    live in several ways — no leverage/notional/spread/quote-age/daily-entry
    gates, ledger cash-guard failures become silent skips instead of
    rejections, and crypto derivatives are sized without contract multipliers.
    Results are directional estimates, not a live-behavior guarantee.
    """
    cfg = config or BacktestConfig()
    if cfg.initial_capital <= 0 or not 0 < cfg.allocation_pct <= 1:
        raise ValueError("invalid backtest capital or allocation")
    normalized = _normalize_bars(bars)
    model = strategy_cls.config_model(**(strategy_config or {}))
    strategy = strategy_cls(model)
    strategy.reset()

    cash = cfg.initial_capital
    position = 0
    quantity = 0.0
    entry_price = 0.0
    entry_bar = 0
    entry_fee = 0.0
    pending: PositionTarget | None = None
    active_stop: float | None = None
    active_trailing: float | None = None
    trail_anchor: float | None = None
    trades: list[BacktestTrade] = []
    curve: list[float] = []
    targets: list[int] = []
    excursion_high = 0.0
    excursion_low = 0.0

    def close_trade(i: int, price: float, *, apply_slippage: bool = True) -> None:
        nonlocal cash, position, quantity, entry_price, entry_fee
        nonlocal active_stop, active_trailing, trail_anchor
        if not position:
            return
        if apply_slippage:
            executed = price * (
                1 - cfg.slippage_pct if position > 0 else 1 + cfg.slippage_pct
            )
        else:
            # Stop/trailing exits already fill AT the stop level; adding
            # slippage on top would double-penalize the exit.
            executed = price
        gross = (executed - entry_price) * quantity * position
        exit_fee = abs(executed * quantity) * cfg.commission_pct
        cash += entry_price * quantity + gross - exit_fee
        trades.append(
            BacktestTrade(
                direction="long" if position > 0 else "short",
                entry_bar=entry_bar,
                entry_price=entry_price,
                exit_bar=i,
                exit_price=executed,
                quantity=quantity,
                pnl=gross - entry_fee - exit_fee,
                fee=entry_fee + exit_fee,
                mfe=excursion_high,
                mae=excursion_low,
            )
        )
        position = 0
        quantity = 0
        entry_price = 0
        entry_fee = 0
        active_stop = None
        active_trailing = None
        trail_anchor = None

    cancel_check = cfg.cancel_check
    for i, bar in enumerate(normalized):
        if cancel_check is not None and i % cfg.cancel_check_every == 0:
            cancel_check()
        if pending is not None:
            target = pending.position
            if target != position:
                close_trade(i, bar.open)
                if target:
                    executed = bar.open * (
                        1 + cfg.slippage_pct if target > 0 else 1 - cfg.slippage_pct
                    )
                    # Never drive cash below zero: with allocation_pct=1 and
                    # commission>0 the old code paid notional + entry fee out
                    # of the same cash (double-dipping → negative cash on
                    # every round-trip). Cap the deployed ratio so the entry
                    # fee fits; if nothing is deployable, skip the trade.
                    if cfg.commission_pct >= 0:
                        effective_ratio = min(
                            cfg.allocation_pct, 1 / (1 + cfg.commission_pct)
                        )
                    else:
                        effective_ratio = cfg.allocation_pct
                    notional = cash * effective_ratio
                    quantity = notional / executed
                    if quantity > 0:
                        entry_fee = notional * cfg.commission_pct
                        cash -= notional + entry_fee
                        position = target
                        entry_price = executed
                        entry_bar = i
                        excursion_high = 0
                        excursion_low = 0
            if pending.clear_risk_exits:
                # Explicit disarm: remove any active stop/trailing so a held
                # position is not forced out by a stale risk exit.
                active_stop = None
                active_trailing = None
                trail_anchor = None
            else:
                active_stop = pending.stop_price
                active_trailing = pending.trailing_distance
                trail_anchor = bar.open if active_trailing else None
            pending = None

        if position:
            if position > 0:
                excursion_high = max(excursion_high, (bar.high - entry_price) * quantity)
                excursion_low = min(excursion_low, (bar.low - entry_price) * quantity)
                # Pessimistic ordering: test the PRIOR stop against the low
                # before raising the trail anchor to the high. A bar whose low
                # came before its high must exit at the older (lower) stop —
                # raising the anchor first would over-credit the exit.
                if active_stop is not None and bar.low <= active_stop:
                    exit_price = min(bar.open, active_stop)
                elif active_trailing:
                    trail_anchor = max(trail_anchor or bar.high, bar.high)
                    active_stop = max(
                        active_stop or float("-inf"), trail_anchor - active_trailing
                    )
                    exit_price = (
                        min(bar.open, active_stop) if bar.low <= active_stop else None
                    )
                else:
                    exit_price = None
                if exit_price is not None:
                    close_trade(i, exit_price, apply_slippage=False)
            else:
                excursion_high = max(excursion_high, (entry_price - bar.low) * quantity)
                excursion_low = min(excursion_low, (entry_price - bar.high) * quantity)
                # Pessimistic ordering mirrors the long side: test the PRIOR
                # stop against the high before lowering the anchor to the low.
                if active_stop is not None and bar.high >= active_stop:
                    exit_price = max(bar.open, active_stop)
                elif active_trailing:
                    trail_anchor = min(trail_anchor or bar.low, bar.low)
                    active_stop = min(
                        active_stop or float("inf"), trail_anchor + active_trailing
                    )
                    exit_price = (
                        max(bar.open, active_stop) if bar.high >= active_stop else None
                    )
                else:
                    exit_price = None
                if exit_price is not None:
                    close_trade(i, exit_price, apply_slippage=False)
                elif (
                    cfg.short_liq_ratio > 0
                    and (bar.high - entry_price) / entry_price >= cfg.short_liq_ratio
                ):
                    # Crude short-liquidation model: force-exit at the current
                    # close once price has run against the position by the
                    # configured ratio from entry.
                    close_trade(i, bar.close, apply_slippage=False)

        strategy.position = position
        target = strategy.process_bar(bar)
        targets.append(target.position)
        pending = target if target.position != position or target.has_risk_order else None

        marked = cash
        if position > 0:
            marked += quantity * bar.close
        elif position < 0:
            marked += entry_price * quantity + (entry_price - bar.close) * quantity
        curve.append(marked)

        if i + 1 == warmup_bars:
            cash = cfg.initial_capital
            position = 0
            quantity = 0
            entry_price = 0
            entry_fee = 0
            pending = None
            active_stop = None
            active_trailing = None
            trail_anchor = None
            trades.clear()
            curve.clear()
            targets.clear()

    if position and normalized:
        close_trade(len(normalized) - 1, normalized[-1].close)
        if curve:
            curve[-1] = cash

    return BacktestResult(
        trades=trades,
        equity_curve=curve,
        targets=targets,
        initial_capital=cfg.initial_capital,
    )
