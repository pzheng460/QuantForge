from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quantforge.strategy.bar import Bar, BarStrategy, PositionTarget


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_capital: float = 100_000
    commission_pct: float = 0
    slippage_pct: float = 0
    allocation_pct: float = 1


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
        bars.append(
            Bar(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
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
    """Run reviewed Python strategy code with next-bar-open order semantics."""
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

    def close_trade(i: int, price: float) -> None:
        nonlocal cash, position, quantity, entry_price, entry_fee
        nonlocal active_stop, active_trailing, trail_anchor
        if not position:
            return
        executed = price * (1 - cfg.slippage_pct if position > 0 else 1 + cfg.slippage_pct)
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

    for i, bar in enumerate(normalized):
        if pending is not None:
            target = pending.position
            if target != position:
                close_trade(i, bar.open)
                if target:
                    executed = bar.open * (
                        1 + cfg.slippage_pct if target > 0 else 1 - cfg.slippage_pct
                    )
                    notional = cash * cfg.allocation_pct
                    quantity = notional / executed
                    entry_fee = notional * cfg.commission_pct
                    cash -= notional + entry_fee
                    position = target
                    entry_price = executed
                    entry_bar = i
                    excursion_high = 0
                    excursion_low = 0
            active_stop = pending.stop_price
            active_trailing = pending.trailing_distance
            trail_anchor = bar.open if active_trailing else None
            pending = None

        if position:
            if position > 0:
                excursion_high = max(excursion_high, (bar.high - entry_price) * quantity)
                excursion_low = min(excursion_low, (bar.low - entry_price) * quantity)
                if active_trailing:
                    trail_anchor = max(trail_anchor or bar.high, bar.high)
                    active_stop = max(
                        active_stop or float("-inf"), trail_anchor - active_trailing
                    )
                if active_stop is not None and bar.low <= active_stop:
                    close_trade(i, min(bar.open, active_stop))
            else:
                excursion_high = max(excursion_high, (entry_price - bar.low) * quantity)
                excursion_low = min(excursion_low, (entry_price - bar.high) * quantity)
                if active_trailing:
                    trail_anchor = min(trail_anchor or bar.low, bar.low)
                    active_stop = min(
                        active_stop or float("inf"), trail_anchor + active_trailing
                    )
                if active_stop is not None and bar.high >= active_stop:
                    close_trade(i, max(bar.open, active_stop))

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
