"""Tests for Pine Script parameter optimizer.

Covers:
- Input extraction from Pine AST
- Grid generation from input metadata
- Optimization run with synthetic data
- Input overrides produce different results
"""

from __future__ import annotations

import math

from quantforge.pine.interpreter.context import BarData
from quantforge.pine.optimize import (
    InputParam,
    extract_pine_inputs,
    generate_grid,
    run_optimization,
)
from quantforge.pine.parser.parser import parse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_bars(n: int = 200, seed: int = 42) -> list[BarData]:
    """Generate synthetic OHLCV bars with a trending + mean-reverting pattern."""
    import random

    bars: list[BarData] = []
    price = 100.0
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


PINE_WITH_INPUTS = """\
//@version=5
strategy("Test Optimizer", overlay=true)

fast_len = input.int(10, title="Fast", minval=5, maxval=20, step=5)
slow_len = input.int(21, title="Slow", minval=15, maxval=40, step=5)

fast_ema = ta.ema(close, fast_len)
slow_ema = ta.ema(close, slow_len)

if ta.crossover(fast_ema, slow_ema)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast_ema, slow_ema)
    strategy.close("Long")
"""

PINE_WITH_FLOAT_INPUTS = """\
//@version=5
strategy("Float Test", overlay=true)

threshold = input.float(0.5, title="Threshold", minval=0.1, maxval=1.0, step=0.1)
period = input.int(14, title="Period", minval=5, maxval=30, step=5)

rsi_val = ta.rsi(close, period)

if rsi_val < threshold * 100
    strategy.entry("Long", strategy.long)

if rsi_val > (1.0 - threshold) * 100
    strategy.close("Long")
"""

PINE_NO_INPUTS = """\
//@version=5
strategy("No Inputs", overlay=true)

fast_ema = ta.ema(close, 10)
slow_ema = ta.ema(close, 21)

if ta.crossover(fast_ema, slow_ema)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast_ema, slow_ema)
    strategy.close("Long")
"""

PINE_AUTO_RANGE_INPUTS = """\
//@version=5
strategy("Auto Range", overlay=true)

fast_len = input.int(10, title="Fast")
slow_len = input.int(21, title="Slow")

fast_ema = ta.ema(close, fast_len)
slow_ema = ta.ema(close, slow_len)

if ta.crossover(fast_ema, slow_ema)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fast_ema, slow_ema)
    strategy.close("Long")
"""


# ---------------------------------------------------------------------------
# Test: input extraction
# ---------------------------------------------------------------------------


class TestExtractInputs:
    def test_extract_int_inputs(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        inputs = extract_pine_inputs(ast)

        assert len(inputs) == 2

        fast = inputs[0]
        assert fast.var_name == "fast_len"
        assert fast.title == "Fast"
        assert fast.input_type == "int"
        assert fast.defval == 10
        assert fast.minval == 5
        assert fast.maxval == 20
        assert fast.step == 5

        slow = inputs[1]
        assert slow.var_name == "slow_len"
        assert slow.title == "Slow"
        assert slow.input_type == "int"
        assert slow.defval == 21
        assert slow.minval == 15
        assert slow.maxval == 40
        assert slow.step == 5

    def test_extract_float_inputs(self) -> None:
        ast = parse(PINE_WITH_FLOAT_INPUTS)
        inputs = extract_pine_inputs(ast)

        assert len(inputs) == 2

        threshold = inputs[0]
        assert threshold.var_name == "threshold"
        assert threshold.title == "Threshold"
        assert threshold.input_type == "float"
        assert threshold.defval == 0.5
        assert threshold.minval == 0.1
        assert threshold.maxval == 1.0
        assert threshold.step == 0.1

    def test_no_inputs_returns_empty(self) -> None:
        ast = parse(PINE_NO_INPUTS)
        inputs = extract_pine_inputs(ast)
        assert inputs == []

    def test_auto_range_inputs(self) -> None:
        ast = parse(PINE_AUTO_RANGE_INPUTS)
        inputs = extract_pine_inputs(ast)
        assert len(inputs) == 2
        # minval/maxval/step should be None (auto-generated during grid)
        for inp in inputs:
            assert inp.minval is None
            assert inp.maxval is None
            assert inp.step is None


# ---------------------------------------------------------------------------
# Test: grid generation
# ---------------------------------------------------------------------------


class TestGenerateGrid:
    def test_grid_with_explicit_ranges(self) -> None:
        inputs = [
            InputParam("fast", "Fast", "int", 10, minval=5, maxval=20, step=5),
            InputParam("slow", "Slow", "int", 21, minval=15, maxval=40, step=5),
        ]
        grid = generate_grid(inputs)

        # Fast: 5, 10, 15, 20 → 4 values
        # Slow: 15, 20, 21(defval), 25, 30, 35, 40 → 7 values
        assert len(grid) == 4 * 7

        # Each grid point is a dict keyed by var_name
        assert all(isinstance(g, dict) for g in grid)
        assert all("fast" in g and "slow" in g for g in grid)

    def test_grid_auto_range(self) -> None:
        inputs = [
            InputParam("fast", "Fast", "int", 10),
        ]
        grid = generate_grid(inputs)
        # Should generate reasonable range around default
        assert len(grid) >= 3
        # All values should be ints (as floats)
        for g in grid:
            assert g["fast"] == int(g["fast"])

    def test_empty_inputs_returns_single_empty(self) -> None:
        grid = generate_grid([])
        assert grid == [{}]

    def test_max_combinations_cap(self) -> None:
        # Create params that would produce a huge grid
        inputs = [
            InputParam(f"p{i}", f"P{i}", "int", 50, minval=1, maxval=100, step=1)
            for i in range(4)
        ]
        grid = generate_grid(inputs, max_combinations=1000)
        assert len(grid) <= 1000

    def test_defval_included_in_grid(self) -> None:
        inputs = [
            InputParam("x", "X", "int", 7, minval=5, maxval=15, step=5),
        ]
        grid = generate_grid(inputs)
        values = [g["x"] for g in grid]
        # defval=7 should be included since it's in range
        assert 7.0 in values


# ---------------------------------------------------------------------------
# Test: optimization run
# ---------------------------------------------------------------------------


class TestRunOptimization:
    def test_basic_optimization(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(300)
        inputs = extract_pine_inputs(ast)
        grid = generate_grid(inputs)

        results = run_optimization(ast, bars, grid)

        assert len(results) == len(grid)
        # Results should be sorted by Sharpe (descending)
        for i in range(len(results) - 1):
            assert results[i].sharpe >= results[i + 1].sharpe

    def test_different_params_produce_different_results(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(300)

        grid = [
            {"fast_len": 5, "slow_len": 30},
            {"fast_len": 15, "slow_len": 20},
        ]

        results = run_optimization(ast, bars, grid)
        assert len(results) == 2

        # At least one metric should differ between different params
        r1, r2 = results
        differs = (
            r1.net_profit != r2.net_profit
            or r1.total_trades != r2.total_trades
            or r1.sharpe != r2.sharpe
        )
        assert differs, "Different params should produce different results"

    def test_sort_by_return(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(200)
        inputs = extract_pine_inputs(ast)
        grid = generate_grid(inputs)

        results = run_optimization(ast, bars, grid, metric="return")

        for i in range(len(results) - 1):
            assert results[i].return_pct >= results[i + 1].return_pct

    def test_sort_by_profit_factor(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(200)
        inputs = extract_pine_inputs(ast)
        grid = generate_grid(inputs)

        results = run_optimization(ast, bars, grid, metric="profit_factor")

        for i in range(len(results) - 1):
            assert results[i].profit_factor >= results[i + 1].profit_factor

    def test_result_fields(self) -> None:
        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(200)

        grid = [{"fast_len": 10, "slow_len": 21}]
        results = run_optimization(ast, bars, grid)

        assert len(results) == 1
        r = results[0]
        assert r.params == {"fast_len": 10, "slow_len": 21}
        assert isinstance(r.net_profit, float)
        assert isinstance(r.return_pct, float)
        assert isinstance(r.total_trades, int)
        assert isinstance(r.sharpe, float)
        assert isinstance(r.max_drawdown, float)
        assert isinstance(r.profit_factor, float)
        assert isinstance(r.equity_curve, list)
        assert len(r.equity_curve) > 0

    def test_single_param_grid(self) -> None:
        """Optimization with a single parameter."""
        pine = """\
//@version=5
strategy("Single Param", overlay=true)

period = input.int(14, title="Period", minval=5, maxval=30, step=5)

rsi_val = ta.rsi(close, period)

if rsi_val < 30
    strategy.entry("Long", strategy.long)

if rsi_val > 70
    strategy.close("Long")
"""
        ast = parse(pine)
        bars = _generate_bars(300)
        inputs = extract_pine_inputs(ast)
        grid = generate_grid(inputs)

        results = run_optimization(ast, bars, grid)
        assert len(results) == len(grid)
        assert all(r.params.get("period") is not None for r in results)


# ---------------------------------------------------------------------------
# Test: input overrides via ExecutionContext
# ---------------------------------------------------------------------------


class TestInputOverrides:
    def test_overrides_change_behaviour(self) -> None:
        """Verify that ctx.inputs overrides change Pine input values."""
        from quantforge.pine.interpreter.context import ExecutionContext
        from quantforge.pine.interpreter.runtime import PineRuntime

        ast = parse(PINE_WITH_INPUTS)
        bars = _generate_bars(200)

        # Run with defaults
        ctx1 = ExecutionContext(bars=list(bars))
        rt1 = PineRuntime(ctx1)
        r1 = rt1.run(ast)

        # Run with fast=5, slow=40
        ctx2 = ExecutionContext(bars=list(bars))
        ctx2.inputs = {"fast_len": 5, "slow_len": 40}
        rt2 = PineRuntime(ctx2)
        r2 = rt2.run(ast)

        # They should produce different trade counts or P&L
        differs = (
            r1.total_trades != r2.total_trades
            or abs(r1.net_profit - r2.net_profit) > 0.01
        )
        assert differs, "Input overrides should change strategy behaviour"
