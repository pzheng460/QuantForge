"""Transpiler accuracy tests — verify transpiled Python produces identical results to Pine interpreter.

For each strategy fixture:
1. Parse the Pine script
2. Run the Pine interpreter on OHLCV data → get trades, PnL
3. Transpile the Pine script to standalone Python
4. Execute the transpiled Python on the SAME data → get trades, PnL
5. Assert: trade count, entry/exit prices, and total PnL are IDENTICAL
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from quantforge.pine.interpreter.builtins.ta import reset_calculators
from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.parser.parser import parse
from quantforge.pine.transpiler.codegen import transpile

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic OHLCV data generators
# ---------------------------------------------------------------------------


def _gen_synthetic_data(n: int = 500, seed: int = 42):
    """Generate deterministic synthetic OHLCV data for testing."""
    rng = random.Random(seed)
    bars: list[BarData] = []
    ohlcv: list[list] = []
    price = 50000.0

    for i in range(n):
        change = rng.uniform(-200, 200)
        o = price
        c = price + change
        h = max(o, c) + rng.uniform(0, 100)
        lo = min(o, c) - rng.uniform(0, 100)
        v = rng.uniform(100, 1000)
        bars.append(BarData(open=o, high=h, low=lo, close=c, volume=v, time=i * 900))
        ohlcv.append([i * 900_000, o, h, lo, c, v])
        price = c

    return bars, ohlcv


def _gen_trending_data(n: int = 500, seed: int = 123):
    """Generate data with clearer trends for more trades."""
    rng = random.Random(seed)
    bars: list[BarData] = []
    ohlcv: list[list] = []
    price = 50000.0
    trend = 1  # 1 = up, -1 = down

    for i in range(n):
        # Switch trend occasionally
        if rng.random() < 0.03:
            trend *= -1
        change = trend * rng.uniform(50, 300) + rng.uniform(-100, 100)
        o = price
        c = price + change
        h = max(o, c) + rng.uniform(0, 150)
        lo = min(o, c) - rng.uniform(0, 150)
        v = rng.uniform(100, 1000)
        bars.append(BarData(open=o, high=h, low=lo, close=c, volume=v, time=i * 900))
        ohlcv.append([i * 900_000, o, h, lo, c, v])
        price = c

    return bars, ohlcv


# ---------------------------------------------------------------------------
# Core parity test helper
# ---------------------------------------------------------------------------


def _run_parity_test(pine_file: str, bars: list[BarData], ohlcv: list[list]) -> None:
    """Run interpreter and transpiled code on same data, assert identical results."""
    source = (FIXTURES_DIR / pine_file).read_text()
    ast = parse(source)

    # --- Run Pine interpreter ---
    reset_calculators()
    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    interp_result = runtime.run(ast)

    # --- Run transpiled Python ---
    code = transpile(ast, pine_source=source)
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102
    tracker = namespace["run"](ohlcv)

    # --- Assert parity ---
    assert len(tracker.trades) == interp_result.total_trades, (
        f"Trade count mismatch: interpreter={interp_result.total_trades}, "
        f"transpiled={len(tracker.trades)}"
    )

    # Compare each trade
    for i, (it, tt) in enumerate(zip(interp_result.trades, tracker.trades)):
        assert it.entry_price == pytest.approx(tt.entry_price, abs=1e-6), (
            f"Trade {i} entry price: interpreter={it.entry_price}, transpiled={tt.entry_price}"
        )
        assert it.exit_price == pytest.approx(tt.exit_price, abs=1e-6), (
            f"Trade {i} exit price: interpreter={it.exit_price}, transpiled={tt.exit_price}"
        )
        assert it.pnl == pytest.approx(tt.pnl, abs=1e-4), (
            f"Trade {i} PnL: interpreter={it.pnl}, transpiled={tt.pnl}"
        )
        # Direction check
        interp_dir = it.direction.value  # Direction enum → string
        assert interp_dir == tt.direction, (
            f"Trade {i} direction: interpreter={interp_dir}, transpiled={tt.direction}"
        )

    # Total PnL
    assert interp_result.net_profit == pytest.approx(tracker.net_profit, abs=1e-4), (
        f"Net PnL mismatch: interpreter={interp_result.net_profit}, "
        f"transpiled={tracker.net_profit}"
    )


# ---------------------------------------------------------------------------
# Test: EMA Cross 5/13
# ---------------------------------------------------------------------------


class TestEMACross513:
    """Parity tests for EMA Cross 5/13 strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("ema_cross_5_13.pine", bars, ohlcv)

    def test_parity_trending(self):
        bars, ohlcv = _gen_trending_data(500, seed=123)
        _run_parity_test("ema_cross_5_13.pine", bars, ohlcv)

    def test_parity_long_series(self):
        bars, ohlcv = _gen_synthetic_data(2000, seed=99)
        _run_parity_test("ema_cross_5_13.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: MACD Cross
# ---------------------------------------------------------------------------


class TestMACDCross:
    """Parity tests for MACD Cross strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("macd_cross.pine", bars, ohlcv)

    def test_parity_trending(self):
        bars, ohlcv = _gen_trending_data(500, seed=123)
        _run_parity_test("macd_cross.pine", bars, ohlcv)

    def test_parity_long_series(self):
        bars, ohlcv = _gen_synthetic_data(2000, seed=99)
        _run_parity_test("macd_cross.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: RSI Mean Reversion
# ---------------------------------------------------------------------------


class TestRSIMeanReversion:
    """Parity tests for RSI Mean Reversion strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("rsi_mean_revert.pine", bars, ohlcv)

    def test_parity_trending(self):
        bars, ohlcv = _gen_trending_data(500, seed=123)
        _run_parity_test("rsi_mean_revert.pine", bars, ohlcv)

    def test_parity_long_series(self):
        bars, ohlcv = _gen_synthetic_data(2000, seed=99)
        _run_parity_test("rsi_mean_revert.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: Bollinger Bands
# ---------------------------------------------------------------------------


class TestBollingerBands:
    """Parity tests for Bollinger Bands strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("bb_strategy.pine", bars, ohlcv)

    def test_parity_trending(self):
        bars, ohlcv = _gen_trending_data(500, seed=123)
        _run_parity_test("bb_strategy.pine", bars, ohlcv)

    def test_parity_long_series(self):
        bars, ohlcv = _gen_synthetic_data(2000, seed=99)
        _run_parity_test("bb_strategy.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: EMA Crossover (literal params, no input.*)
# ---------------------------------------------------------------------------


class TestEMACrossover:
    """Parity tests for EMA Crossover (literal params) strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("ema_cross.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: RSI Strategy (literal params, no input.*)
# ---------------------------------------------------------------------------


class TestRSIStrategy:
    """Parity tests for RSI Strategy (literal params) strategy."""

    def test_parity_synthetic(self):
        bars, ohlcv = _gen_synthetic_data(500, seed=42)
        _run_parity_test("rsi_strategy.pine", bars, ohlcv)


# ---------------------------------------------------------------------------
# Test: Transpiled code compiles and has expected structure
# ---------------------------------------------------------------------------


class TestTranspilerStructure:
    """Verify the transpiled code structure."""

    @pytest.fixture
    def ema_code(self):
        source = (FIXTURES_DIR / "ema_cross_5_13.pine").read_text()
        ast = parse(source)
        return transpile(ast, pine_source=source)

    def test_compiles(self, ema_code):
        compile(ema_code, "<transpiled>", "exec")

    def test_has_run_function(self, ema_code):
        ns: dict = {}
        exec(ema_code, ns)  # noqa: S102
        assert "run" in ns
        assert callable(ns["run"])

    def test_has_fetch_ohlcv(self, ema_code):
        ns: dict = {}
        exec(ema_code, ns)  # noqa: S102
        assert "fetch_ohlcv" in ns
        assert callable(ns["fetch_ohlcv"])

    def test_has_strategy_tracker(self, ema_code):
        ns: dict = {}
        exec(ema_code, ns)  # noqa: S102
        assert "StrategyTracker" in ns

    def test_no_pine_interpreter_imports(self, ema_code):
        assert "from quantforge.pine.interpreter" not in ema_code
        assert "PineRuntime" not in ema_code
        assert "ExecutionContext" not in ema_code

    def test_strategy_name_extracted(self):
        source = (FIXTURES_DIR / "ema_cross_5_13.pine").read_text()
        ast = parse(source)
        code = transpile(ast, pine_source=source)
        assert "EMACross5_13" in code

    def test_all_fixtures_compile(self):
        """Every .pine fixture in the fixtures dir should transpile and compile."""
        for pine_file in FIXTURES_DIR.glob("*.pine"):
            source = pine_file.read_text()
            ast = parse(source)
            code = transpile(ast, pine_source=source)
            try:
                compile(code, str(pine_file), "exec")
            except SyntaxError as e:
                pytest.fail(f"Transpiled {pine_file.name} has syntax error: {e}")
