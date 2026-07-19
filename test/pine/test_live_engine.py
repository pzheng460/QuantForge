"""Tests for Pine live engine — incremental vs batch parity.

Verifies that feeding bars one-by-one via ``process_bar`` produces
IDENTICAL trades to the existing ``run()`` method (full materialization).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.live.order_bridge import OrderBridge
from quantforge.pine.parser.parser import parse

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_bars(n: int = 200, seed: int = 42) -> list[BarData]:
    """Generate synthetic OHLCV bars with a trending + mean-reverting pattern."""
    bars: list[BarData] = []
    price = 100.0
    import random

    rng = random.Random(seed)

    for i in range(n):
        trend = 0.05 * math.sin(2 * math.pi * i / 50)
        noise = rng.gauss(0, 0.5)
        price = max(10.0, price + trend + noise)
        o = price
        h = price + abs(rng.gauss(0, 0.3))
        lo = price - abs(rng.gauss(0, 0.3))
        c = price + rng.gauss(0, 0.2)
        c = max(lo, min(h, c))
        v = rng.uniform(100, 1000)
        bars.append(
            BarData(open=o, high=h, low=lo, close=c, volume=v, time=1000000 + i * 60)
        )

    return bars


def _run_batch(source: str, bars: list[BarData]):
    """Run Pine script in batch mode (existing run() method)."""
    ast = parse(source)
    ctx = ExecutionContext(bars=list(bars))
    rt = PineRuntime(ctx)
    return rt.run(ast)


def _run_incremental(source: str, bars: list[BarData]):
    """Run Pine script in incremental mode (one bar at a time)."""
    ast = parse(source)
    ctx = ExecutionContext()
    rt = PineRuntime(ctx)
    rt.init_incremental(ast)

    for bar in bars:
        rt.process_bar(bar)

    return rt.finalize()


def _assert_trades_equal(batch_result, incr_result, label: str = "") -> None:
    """Assert batch and incremental results produce identical trades."""
    bt = batch_result.trades
    it = incr_result.trades

    assert len(bt) == len(it), (
        f"{label}: trade count mismatch: batch={len(bt)} vs incremental={len(it)}"
    )

    for i, (b, ic) in enumerate(zip(bt, it)):
        assert b.entry_bar == ic.entry_bar, f"{label} trade {i}: entry_bar mismatch"
        assert b.exit_bar == ic.exit_bar, f"{label} trade {i}: exit_bar mismatch"
        assert abs(b.entry_price - ic.entry_price) < 1e-10, (
            f"{label} trade {i}: entry_price mismatch"
        )
        assert abs(b.exit_price - ic.exit_price) < 1e-10, (
            f"{label} trade {i}: exit_price mismatch"
        )
        assert abs(b.pnl - ic.pnl) < 1e-10, f"{label} trade {i}: pnl mismatch"
        assert b.direction == ic.direction, f"{label} trade {i}: direction mismatch"


# ---------------------------------------------------------------------------
# Tests: incremental vs batch parity
# ---------------------------------------------------------------------------


class TestIncrementalParity:
    """Verify incremental execution matches batch execution exactly."""

    @pytest.fixture(params=["ema_cross.pine", "rsi_strategy.pine", "macd_cross.pine"])
    def fixture_name(self, request):
        return request.param

    def test_parity_with_fixture(self, fixture_name: str) -> None:
        """Incremental and batch produce identical trades for fixture strategies."""
        source = (FIXTURES / fixture_name).read_text()
        bars = _generate_bars(300)

        batch = _run_batch(source, bars)
        incr = _run_incremental(source, bars)

        _assert_trades_equal(batch, incr, label=fixture_name)

        # Equity curves should match
        assert len(batch.equity_curve) == len(incr.equity_curve)
        for i, (be, ie) in enumerate(zip(batch.equity_curve, incr.equity_curve)):
            assert abs(be - ie) < 1e-8, f"equity mismatch at bar {i}"

    def test_parity_bb_strategy(self) -> None:
        source = (FIXTURES / "bb_strategy.pine").read_text()
        bars = _generate_bars(400)

        batch = _run_batch(source, bars)
        incr = _run_incremental(source, bars)

        _assert_trades_equal(batch, incr, label="bb_strategy")

    def test_parity_ema_cross_5_13(self) -> None:
        source = (FIXTURES / "ema_cross_5_13.pine").read_text()
        bars = _generate_bars(250)

        batch = _run_batch(source, bars)
        incr = _run_incremental(source, bars)

        _assert_trades_equal(batch, incr, label="ema_cross_5_13")

    def test_parity_rsi_mean_revert(self) -> None:
        source = (FIXTURES / "rsi_mean_revert.pine").read_text()
        bars = _generate_bars(350)

        batch = _run_batch(source, bars)
        incr = _run_incremental(source, bars)

        _assert_trades_equal(batch, incr, label="rsi_mean_revert")


class TestIncrementalWarmup:
    """Test warmup + live bar processing."""

    def test_warmup_then_live(self) -> None:
        """Feed warmup bars, then live bars — result same as full batch."""
        source = (FIXTURES / "ema_cross.pine").read_text()
        bars = _generate_bars(200)
        warmup = bars[:100]
        live = bars[100:]

        # Batch: all bars at once
        batch = _run_batch(source, bars)

        # Incremental: warmup + live
        ast = parse(source)
        ctx = ExecutionContext()
        rt = PineRuntime(ctx)
        rt.init_incremental(ast)

        for bar in warmup:
            rt.process_bar(bar)

        for bar in live:
            rt.process_bar(bar)

        incr = rt.finalize()

        _assert_trades_equal(batch, incr, label="warmup_then_live")

    def test_empty_warmup(self) -> None:
        """No warmup bars — should still work."""
        source = (FIXTURES / "ema_cross.pine").read_text()
        bars = _generate_bars(100)

        batch = _run_batch(source, bars)

        ast = parse(source)
        ctx = ExecutionContext()
        rt = PineRuntime(ctx)
        rt.init_incremental(ast)

        for bar in bars:
            rt.process_bar(bar)

        incr = rt.finalize()

        _assert_trades_equal(batch, incr, label="empty_warmup")


class TestSignalCallbacks:
    """Test that signal callbacks fire correctly."""

    def test_entry_callback_fires(self) -> None:
        """OrderBridge.on_entry is called when strategy.entry() fires."""
        source = (FIXTURES / "ema_cross.pine").read_text()
        bars = _generate_bars(200)

        bridge = OrderBridge(demo=True)

        ast = parse(source)
        ctx = ExecutionContext()
        rt = PineRuntime(ctx)
        rt.init_incremental(ast)

        assert rt.strategy_ctx is not None
        rt.strategy_ctx.set_signal_callbacks(
            on_entry=bridge.on_entry,
            on_close=bridge.on_close,
            on_exit=bridge.on_exit,
        )

        for bar in bars:
            rt.process_bar(bar)

        result = rt.finalize()

        # Bridge should have captured signals
        assert len(bridge.signals) > 0, "No signals captured"

        # Count entry signals — should be >= number of trades (each trade has an entry)
        entry_signals = [s for s in bridge.signals if s.action == "entry"]
        assert len(entry_signals) >= result.total_trades, (
            f"Expected >= {result.total_trades} entry signals, got {len(entry_signals)}"
        )

    def test_close_callback_fires(self) -> None:
        """OrderBridge.on_close is called when strategy.close() fires."""
        source = (FIXTURES / "ema_cross.pine").read_text()
        bars = _generate_bars(200)

        bridge = OrderBridge(demo=True)

        ast = parse(source)
        ctx = ExecutionContext()
        rt = PineRuntime(ctx)
        rt.init_incremental(ast)

        assert rt.strategy_ctx is not None
        rt.strategy_ctx.set_signal_callbacks(
            on_entry=bridge.on_entry,
            on_close=bridge.on_close,
            on_exit=bridge.on_exit,
        )

        for bar in bars:
            rt.process_bar(bar)

        rt.finalize()

        close_signals = [s for s in bridge.signals if s.action == "close"]
        # EMA cross uses strategy.close("Long") so close_signals should exist
        assert len(close_signals) > 0, "No close signals captured"


class TestProcessBarReturn:
    """Test that process_bar returns new orders placed during the bar."""

    def test_returns_orders(self) -> None:
        source = (FIXTURES / "ema_cross.pine").read_text()
        bars = _generate_bars(200)

        ast = parse(source)
        ctx = ExecutionContext()
        rt = PineRuntime(ctx)
        rt.init_incremental(ast)

        total_orders = 0
        for bar in bars:
            orders = rt.process_bar(bar)
            total_orders += len(orders)

        # Should have captured some orders
        assert total_orders > 0, "No orders returned from process_bar"
