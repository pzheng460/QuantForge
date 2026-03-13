"""Simple backtester for the declarative Strategy API.

Runs a Strategy subclass on OHLCV bar data and returns trades + equity curve.

Usage:
    from quantforge.dsl.backtest import backtest
    result = backtest(EMACross, bars, fast_period=8, slow_period=21)
    print(result.total_return_pct, result.trade_count)
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from quantforge.dsl.api import Bar

if TYPE_CHECKING:
    from quantforge.dsl.api import Strategy


@dataclasses.dataclass
class Trade:
    """A completed round-trip trade."""

    direction: int  # 1=long, -1=short
    entry_bar: int
    entry_price: float
    exit_bar: int
    exit_price: float
    pnl_pct: float

    @property
    def pnl(self) -> float:
        if self.direction == 1:
            return (self.exit_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.exit_price) / self.entry_price


@dataclasses.dataclass
class BacktestResult:
    """Results from a backtest run."""

    trades: list[Trade]
    signals: list[int]
    equity_curve: list[float]
    initial_capital: float

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def total_return_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        return (self.equity_curve[-1] / self.initial_capital - 1) * 100

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl_pct > 0)
        return wins / len(self.trades)

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100


def backtest(
    strategy_cls: type[Strategy],
    bars: list[list | tuple],
    *,
    initial_capital: float = 100_000.0,
    commission_pct: float = 0.0005,
    **params,
) -> BacktestResult:
    """Run a backtest on OHLCV bar data.

    Args:
        strategy_cls: Strategy subclass to test.
        bars: List of [timestamp, open, high, low, close, volume] or
              list of (open, high, low, close, volume).
        initial_capital: Starting capital.
        commission_pct: Commission per trade (fraction, e.g. 0.0005 = 0.05%).
        **params: Parameter overrides passed to strategy constructor.

    Returns:
        BacktestResult with trades, signals, and equity curve.
    """
    strat = strategy_cls(**params)

    # Normalize bars
    normalized: list[Bar] = []
    for b in bars:
        if len(b) >= 6:
            # [timestamp, open, high, low, close, volume]
            normalized.append(
                Bar(open=b[1], high=b[2], low=b[3], close=b[4], volume=b[5])
            )
        elif len(b) >= 5:
            # (open, high, low, close, volume)
            normalized.append(
                Bar(open=b[0], high=b[1], low=b[2], close=b[3], volume=b[4])
            )
        else:
            raise ValueError(f"Bar must have at least 5 values (OHLCV), got {len(b)}")

    # Run strategy with 1-bar signal delay execution
    signals: list[int] = []
    trades: list[Trade] = []
    equity_curve: list[float] = []

    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    entry_bar = 0
    capital = initial_capital
    pending_signal = 0  # signal from previous bar, executed on current bar's open

    for i, bar in enumerate(normalized):
        # Execute pending signal at this bar's open
        if pending_signal != 0 and i > 0:
            exec_price = bar.open

            if pending_signal in (1, -1) and position == 0:
                # Open position
                commission = capital * commission_pct
                capital -= commission
                position = pending_signal
                entry_price = exec_price
                entry_bar = i

            elif (
                pending_signal in (1, -1)
                and position != 0
                and position != pending_signal
            ):
                # Close opposite position + open new
                if position == 1:
                    pnl_pct = (exec_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - exec_price) / entry_price
                commission = capital * commission_pct
                capital *= 1 + pnl_pct
                capital -= commission
                trades.append(
                    Trade(
                        direction=position,
                        entry_bar=entry_bar,
                        entry_price=entry_price,
                        exit_bar=i,
                        exit_price=exec_price,
                        pnl_pct=pnl_pct,
                    )
                )
                # Open new position in opposite direction
                commission = capital * commission_pct
                capital -= commission
                position = pending_signal
                entry_price = exec_price
                entry_bar = i

            elif pending_signal == 2 and position != 0:
                # Close position
                if position == 1:
                    pnl_pct = (exec_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - exec_price) / entry_price
                commission = capital * commission_pct
                capital *= 1 + pnl_pct
                capital -= commission
                trades.append(
                    Trade(
                        direction=position,
                        entry_bar=entry_bar,
                        entry_price=entry_price,
                        exit_bar=i,
                        exit_price=exec_price,
                        pnl_pct=pnl_pct,
                    )
                )
                position = 0
                entry_price = 0.0

        # Generate signal for this bar
        signal = strat._process_bar(bar)
        signals.append(signal)
        pending_signal = signal

        # Update equity
        if position == 1:
            unrealized = (bar.close - entry_price) / entry_price
            equity_curve.append(capital * (1 + unrealized))
        elif position == -1:
            unrealized = (entry_price - bar.close) / entry_price
            equity_curve.append(capital * (1 + unrealized))
        else:
            equity_curve.append(capital)

    return BacktestResult(
        trades=trades,
        signals=signals,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
    )
