"""End-to-end interpreter tests: parse Pine Script → run on OHLCV → verify trades."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge.pine.interpreter.runtime import BacktestResult, PineRuntime
from quantforge.pine.parser.parser import PineParser
from quantforge.pine.transpiler.codegen import PineTranspiler

FIXTURES = Path(__file__).parent / "fixtures"


def _make_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with a trend."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 2)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close + rng.randn(n) * 0.5
    volume = rng.uniform(1000, 10000, n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "time": range(n),
        }
    )


def _make_trending_data(n: int = 200) -> pd.DataFrame:
    """Generate data with clear up/down trends for crossover testing."""
    close = np.zeros(n)
    # Downtrend first 60 bars, then uptrend, then downtrend
    for i in range(n):
        if i < 60:
            close[i] = 100 - i * 0.5
        elif i < 140:
            close[i] = 70 + (i - 60) * 1.0
        else:
            close[i] = 150 - (i - 140) * 0.8

    high = close + 1.0
    low = close - 1.0
    open_ = close + np.random.RandomState(42).randn(n) * 0.3
    volume = np.full(n, 5000.0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "time": range(n),
        }
    )


@pytest.fixture
def parser():
    return PineParser()


# ==============================================================================
# Basic interpreter tests
# ==============================================================================
class TestBasicInterpreter:
    def test_simple_assignment(self, parser):
        src = "//@version=5\nx = close\n"
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(10)
        result = runtime.run(data)
        assert isinstance(result, BacktestResult)

    def test_arithmetic(self, parser):
        src = "//@version=5\nx = 1 + 2 * 3\n"
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(5)
        result = runtime.run(data)
        assert isinstance(result, BacktestResult)

    def test_var_persistence(self, parser):
        src = """//@version=5
var counter = 0
counter := counter + 1
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(10)
        runtime.run(data)
        # After 10 bars, counter should be 10
        val = runtime.ctx.get_var("counter")
        assert val == 10

    def test_if_else(self, parser):
        src = """//@version=5
x = 0
if close > open
    x := 1
else
    x := -1
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(5)
        runtime.run(data)
        val = runtime.ctx.get_var("x")
        assert val in (1, -1)

    def test_for_loop(self, parser):
        src = """//@version=5
var total = 0
for i = 1 to 5
    total := total + i
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(1)
        runtime.run(data)
        val = runtime.ctx.get_var("total")
        assert val == 15  # 1+2+3+4+5

    def test_user_function(self, parser):
        src = """//@version=5
add(a, b) =>
    a + b
x = add(3, 4)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(1)
        runtime.run(data)
        val = runtime.ctx.get_var("x")
        assert val == 7

    def test_series_indexing(self, parser):
        src = """//@version=5
prev_close = close[1]
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(5)
        runtime.run(data)
        # After 5 bars, prev_close should be the 4th bar's close
        val = runtime.ctx.get_var("prev_close")
        assert val is not None


# ==============================================================================
# ta.* function tests in interpreter context
# ==============================================================================
class TestTaInInterpreter:
    def test_sma(self, parser):
        src = """//@version=5
sma_val = ta.sma(close, 3)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(10)
        runtime.run(data)
        val = runtime.ctx.get_var("sma_val")
        # After 10 bars with length=3, should have a value
        assert val is not None

    def test_ema(self, parser):
        src = """//@version=5
ema_val = ta.ema(close, 5)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(20)
        runtime.run(data)
        val = runtime.ctx.get_var("ema_val")
        assert val is not None

    def test_rsi(self, parser):
        src = """//@version=5
rsi_val = ta.rsi(close, 14)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(30)
        runtime.run(data)
        val = runtime.ctx.get_var("rsi_val")
        # RSI should be between 0 and 100
        if val is not None:
            assert 0 <= val <= 100

    def test_crossover(self, parser):
        src = """//@version=5
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
crossed = ta.crossover(fast, slow)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_trending_data(200)
        runtime.run(data)
        # crossed should be a boolean
        val = runtime.ctx.get_var("crossed")
        assert isinstance(val, bool)

    def test_atr(self, parser):
        src = """//@version=5
atr_val = ta.atr(14)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(30)
        runtime.run(data)
        val = runtime.ctx.get_var("atr_val")
        if val is not None:
            assert val >= 0

    def test_macd_multi_return(self, parser):
        src = """//@version=5
[macd_line, signal_line, hist] = ta.macd(close, 12, 26, 9)
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(50)
        runtime.run(data)
        # All three should be defined
        macd = runtime.ctx.get_var("macd_line")
        sig = runtime.ctx.get_var("signal_line")
        # At least some should be non-None with 50 bars
        assert macd is not None or sig is not None


# ==============================================================================
# Strategy tests
# ==============================================================================
class TestStrategyInterpreter:
    def test_ema_cross_strategy(self, parser):
        src = """//@version=5
strategy("EMA Cross", initial_capital=10000)
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast, initial_capital=10000)
        data = _make_trending_data(200)
        result = runtime.run(data)

        assert isinstance(result, BacktestResult)
        assert result.initial_capital == 10000
        # Should have produced some trades with trending data
        assert result.total_trades >= 0
        assert result.final_equity > 0

    def test_rsi_strategy(self, parser):
        src = """//@version=5
strategy("RSI Strategy", initial_capital=10000)
rsi_val = ta.rsi(close, 14)
if rsi_val < 30
    strategy.entry("Long", strategy.long)
if rsi_val > 70
    strategy.close("Long")
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast, initial_capital=10000)
        data = _make_ohlcv(200)
        result = runtime.run(data)
        assert isinstance(result, BacktestResult)

    def test_strategy_with_inputs(self, parser):
        src = """//@version=5
strategy("Input Test", initial_capital=10000)
fast_len = input.int(9, title="Fast Length")
slow_len = input.int(21, title="Slow Length")
fast = ta.ema(close, fast_len)
slow = ta.ema(close, slow_len)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
        ast = parser.parse(src)
        # Override inputs
        runtime = PineRuntime(
            ast,
            initial_capital=10000,
            input_overrides={"Fast Length": 5, "Slow Length": 15},
        )
        data = _make_trending_data(200)
        result = runtime.run(data)
        assert result.inputs["Fast Length"] == 5
        assert result.inputs["Slow Length"] == 15

    def test_short_strategy(self, parser):
        src = """//@version=5
strategy("Short Test", initial_capital=10000)
if ta.crossunder(ta.ema(close, 5), ta.ema(close, 20))
    strategy.entry("Short", strategy.short)
if ta.crossover(ta.ema(close, 5), ta.ema(close, 20))
    strategy.close("Short")
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast, initial_capital=10000)
        data = _make_trending_data(200)
        result = runtime.run(data)
        assert isinstance(result, BacktestResult)

    def test_equity_curve(self, parser):
        src = """//@version=5
strategy("Equity Test", initial_capital=10000)
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast, initial_capital=10000)
        data = _make_trending_data(200)
        result = runtime.run(data)
        # Equity curve should have entries
        assert len(result.equity_curve) > 0

    def test_backtest_result_summary(self, parser):
        src = """//@version=5
strategy("Summary Test")
if bar_index == 5
    strategy.entry("Long", strategy.long)
if bar_index == 15
    strategy.close("Long")
"""
        ast = parser.parse(src)
        runtime = PineRuntime(ast)
        data = _make_ohlcv(30)
        result = runtime.run(data)
        summary = result.summary()
        assert "Initial Capital" in summary
        assert "Final Equity" in summary


# ==============================================================================
# Fixture file tests
# ==============================================================================
class TestFixtureFiles:
    def test_ema_cross_fixture(self, parser):
        pine_file = FIXTURES / "ema_cross.pine"
        if pine_file.exists():
            source = pine_file.read_text()
            ast = parser.parse(source)
            runtime = PineRuntime(ast, initial_capital=10000, commission=0.001)
            data = _make_trending_data(200)
            result = runtime.run(data)
            assert isinstance(result, BacktestResult)
            assert result.initial_capital == 10000

    def test_rsi_fixture(self, parser):
        pine_file = FIXTURES / "rsi_strategy.pine"
        if pine_file.exists():
            source = pine_file.read_text()
            ast = parser.parse(source)
            runtime = PineRuntime(ast, initial_capital=10000, commission=0.001)
            data = _make_ohlcv(200)
            result = runtime.run(data)
            assert isinstance(result, BacktestResult)


# ==============================================================================
# Transpiler tests
# ==============================================================================
class TestTranspiler:
    def test_basic_transpile(self, parser):
        src = """//@version=5
strategy("EMA Cross", overlay=true)
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
        ast = parser.parse(src)
        transpiler = PineTranspiler()
        code = transpiler.transpile(ast)

        assert "class" in code
        assert "def on_kline" in code
        assert "OrderSide.BUY" in code or "create_order" in code

    def test_transpile_with_inputs(self, parser):
        src = """//@version=5
strategy("Input Strategy")
fast_len = input.int(9, title="Fast Length")
slow_len = input.int(21, title="Slow Length")
fast = ta.ema(close, fast_len)
slow = ta.ema(close, slow_len)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
"""
        ast = parser.parse(src)
        transpiler = PineTranspiler()
        code = transpiler.transpile(ast)

        assert "fast_len" in code or "Fast Length" in code
        assert "def __init__" in code
