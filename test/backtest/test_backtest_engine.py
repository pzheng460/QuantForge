"""Shared backtest engine: cash-sufficiency, exit-slippage, cancellation.

These lock in the review findings:
* an entry at allocation_pct=1 with commission>0 must never drive cash below
  zero (the fee used to be paid out of the same cash as the notional);
* stop/trailing exits fill AT the stop level — no extra slippage on top;
* the cancel_check hook aborts a CPU-bound run promptly.
"""

from __future__ import annotations

import pytest

from quantforge.backtest import BacktestConfig, run_backtest
from quantforge.strategy.bar import BarStrategy, PositionTarget

# 1h bars, flat at 100 except a controlled segment.
BASE = 100.0


def _bars(rows: list[tuple[float, float, float, float]]) -> list[list]:
    out = []
    for i, (opn, high, low, close) in enumerate(rows):
        out.append([i * 3_600_000, opn, high, low, close, 1000.0])
    return out


class _LongFrom(BarStrategy):
    """Open long at bar index >= open_at (target 1), else flat."""

    def __init__(self, config, open_at: int = 1):
        super().__init__(config)
        self._open_at = open_at
        self._i = 0

    def on_bar(self, bar) -> PositionTarget:
        self._i += 1
        if self._i >= self._open_at and self.position == 0:
            return PositionTarget(position=1)
        return PositionTarget(position=self.position)

    def reset(self) -> None:
        super().reset()
        self._i = 0


class _LongStop(BarStrategy):
    """Long with a hard stop that is always refreshed to ``stop_price``."""

    def __init__(self, config, open_at: int = 2, stop_price: float = 99.0):
        super().__init__(config)
        self._open_at = open_at
        self._stop_price = stop_price
        self._i = 0

    def on_bar(self, bar) -> PositionTarget:
        self._i += 1
        if self._i == self._open_at:
            return PositionTarget(position=1, stop_price=self._stop_price)
        return PositionTarget(position=self.position)

    def reset(self) -> None:
        super().reset()
        self._i = 0


def test_allocation_full_with_fees_never_goes_cash_negative():
    """Regression: old code did cash -= notional + fee with allocation 1.0,
    going negative whenever commission>0. The clamped engine must never show
    negative equity even when the position value collapses."""
    # Crash the price to ~0 after entry so marked equity = cash alone.
    rows = [(BASE, BASE, BASE, BASE)] * 2
    rows += [(BASE, BASE, BASE, 0.001)] * 20
    bars = _bars(rows)
    result = run_backtest(
        _LongFrom,
        bars,
        config=BacktestConfig(
            initial_capital=100_000,
            commission_pct=0.01,
            allocation_pct=1.0,
        ),
        warmup_bars=0,
    )
    # At the crash close, position value ~= qty * 0.001, so equity ≈ cash,
    # which must stay >= 0.
    assert min(result.equity_curve) >= 0


def test_stop_exit_fills_at_stop_no_double_slippage():
    rows = [
        (BASE, BASE, BASE, BASE),
        (BASE, BASE, BASE, BASE),
        (102.0, 102.0, 102.0, 102.0),  # open long, stop 99
        (103.0, 104.0, 98.0, 102.0),   # low breaks stop at 99 → fill AT stop
    ]
    result = run_backtest(
        _LongStop,
        _bars(rows),
        config=BacktestConfig(
            initial_capital=100_000,
            commission_pct=0,
            slippage_pct=0.005,  # would make a double-penalized fill 98.505
        ),
        warmup_bars=0,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    # The old code filled at stop_price * (1 - slippage) — now it must be
    # exactly the stop level.
    assert trade.exit_price == pytest.approx(99.0)


def test_cancel_check_aborts_promptly():
    calls = {"n": 0}

    def cancel() -> None:
        calls["n"] += 1
        raise RuntimeError("cancelled by test")

    rows = [(BASE, BASE, BASE, BASE)] * 2000
    with pytest.raises(RuntimeError, match="cancelled by test"):
        run_backtest(
            _LongFrom,
            _bars(rows),
            config=BacktestConfig(
                initial_capital=100_000,
                cancel_check=cancel,
                cancel_check_every=64,
            ),
            warmup_bars=0,
        )
    assert 0 < calls["n"] < 2000
