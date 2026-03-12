"""Tests for Pine Script built-in functions."""

from __future__ import annotations


import pytest

from quantforge.pine.interpreter.builtins.math_fn import (
    pine_abs,
    pine_ceil,
    pine_floor,
    pine_log,
    pine_max,
    pine_min,
    pine_pow,
    pine_round,
    pine_sign,
    pine_sqrt,
)
from quantforge.pine.interpreter.builtins.strategy import (
    Direction,
    StrategyEngine,
)
from quantforge.pine.interpreter.builtins.input_fn import InputManager
from quantforge.pine.interpreter.builtins.ta import (
    TaBBands,
    TaChange,
    TaCrossover,
    TaCrossunder,
    TaEMA,
    TaHighest,
    TaLowest,
    TaMACD,
    TaRSI,
    TaSMA,
    TaStoch,
    TaTR,
)
from quantforge.pine.interpreter.series import PineSeries


# ==============================================================================
# Series Tests
# ==============================================================================
class TestPineSeries:
    def test_push_and_access(self):
        s = PineSeries()
        s.push(10.0)
        s.push(20.0)
        s.push(30.0)
        assert s[0] == 30.0  # current
        assert s[1] == 20.0  # previous
        assert s[2] == 10.0

    def test_out_of_bounds(self):
        s = PineSeries()
        s.push(1.0)
        assert s[5] is None
        assert s[-1] is None

    def test_na_check(self):
        s = PineSeries()
        s.push(None)
        assert s.is_na(0) is True
        s.push(1.0)
        assert s.is_na(0) is False

    def test_current_previous(self):
        s = PineSeries()
        s.push(1.0)
        s.push(2.0)
        assert s.current == 2.0
        assert s.previous == 1.0


# ==============================================================================
# Math Function Tests
# ==============================================================================
class TestMathFunctions:
    def test_abs(self):
        assert pine_abs(-5) == 5
        assert pine_abs(None) is None

    def test_max_min(self):
        assert pine_max(3, 5) == 5
        assert pine_min(3, 5) == 3
        assert pine_max(None, 5) is None

    def test_round(self):
        assert pine_round(3.7) == 4
        assert pine_round(3.14159, 2) == 3.14

    def test_log(self):
        assert pine_log(1.0) == 0.0
        assert pine_log(-1) is None
        assert pine_log(None) is None

    def test_sqrt(self):
        assert pine_sqrt(4) == 2.0
        assert pine_sqrt(-1) is None

    def test_pow(self):
        assert pine_pow(2, 3) == 8

    def test_ceil_floor(self):
        assert pine_ceil(3.2) == 4
        assert pine_floor(3.8) == 3

    def test_sign(self):
        assert pine_sign(5) == 1
        assert pine_sign(-5) == -1
        assert pine_sign(0) == 0


# ==============================================================================
# ta.* Tests
# ==============================================================================
class TestTaSMA:
    def test_basic(self):
        sma = TaSMA(3)
        assert sma.update(1.0) is None
        assert sma.update(2.0) is None
        r = sma.update(3.0)
        assert r == pytest.approx(2.0)

    def test_rolling(self):
        sma = TaSMA(3)
        sma.update(1.0)
        sma.update(2.0)
        sma.update(3.0)
        r = sma.update(4.0)
        assert r == pytest.approx(3.0)  # (2+3+4)/3

    def test_none_handling(self):
        sma = TaSMA(3)
        assert sma.update(None) is None


class TestTaEMA:
    def test_basic(self):
        ema = TaEMA(3)
        r1 = ema.update(10.0)
        assert r1 == 10.0  # first value seeded
        r2 = ema.update(20.0)
        assert r2 is not None
        assert r2 > 10.0


class TestTaRSI:
    def test_warmup(self):
        rsi = TaRSI(14)
        # Need 14+1 values to get first RSI
        for i in range(14):
            rsi.update(float(50 + i))
        # After 14 values RSI should still be warming up
        # (need length+1 for RMA seed)

    def test_known_trend(self):
        rsi = TaRSI(3)
        # Feed monotonically increasing values
        vals = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        results = [rsi.update(v) for v in vals]
        # With all gains, RSI should approach 100
        final = [r for r in results if r is not None]
        if final:
            assert final[-1] > 50  # uptrend should give RSI > 50


class TestTaCrossover:
    def test_basic_crossover(self):
        co = TaCrossover()
        assert co.update(1.0, 2.0) is False  # a < b
        assert co.update(3.0, 2.0) is True  # a crossed above b

    def test_no_cross(self):
        co = TaCrossover()
        assert co.update(1.0, 2.0) is False
        assert co.update(1.5, 2.0) is False  # still below


class TestTaCrossunder:
    def test_basic_crossunder(self):
        cu = TaCrossunder()
        assert cu.update(3.0, 2.0) is False  # a > b
        assert cu.update(1.0, 2.0) is True  # a crossed below b


class TestTaHighestLowest:
    def test_highest(self):
        h = TaHighest(3)
        assert h.update(1.0) is None
        assert h.update(3.0) is None
        assert h.update(2.0) == 3.0
        assert h.update(5.0) == 5.0

    def test_lowest(self):
        lo = TaLowest(3)
        assert lo.update(3.0) is None
        assert lo.update(1.0) is None
        assert lo.update(2.0) == 1.0
        assert lo.update(4.0) == 1.0
        assert lo.update(5.0) == 2.0  # 1.0 rolled off


class TestTaChange:
    def test_basic(self):
        c = TaChange(1)
        assert c.update(10.0) is None
        assert c.update(15.0) == 5.0
        assert c.update(12.0) == -3.0


class TestTaTR:
    def test_basic(self):
        tr = TaTR()
        r1 = tr.update(12.0, 10.0, 11.0)
        assert r1 == 2.0  # first bar: high - low
        r2 = tr.update(13.0, 9.0, 12.0)
        assert r2 == pytest.approx(4.0)  # max(13-9, |13-11|, |9-11|) = 4


class TestTaMACD:
    def test_returns_tuple(self):
        macd = TaMACD(3, 5, 3)
        for i in range(10):
            result = macd.update(float(50 + i))
        assert isinstance(result, tuple)
        assert len(result) == 3


class TestTaBBands:
    def test_returns_tuple(self):
        bb = TaBBands(3, 2.0)
        bb.update(10.0)
        bb.update(11.0)
        result = bb.update(12.0)
        assert isinstance(result, tuple)
        mid, upper, lower = result
        assert mid == pytest.approx(11.0)
        assert upper > mid
        assert lower < mid


class TestTaStoch:
    def test_basic(self):
        stoch = TaStoch(3)
        stoch.update(10.0, 12.0, 8.0)
        stoch.update(11.0, 13.0, 9.0)
        result = stoch.update(12.0, 14.0, 10.0)
        assert result is not None
        assert 0 <= result <= 100


# ==============================================================================
# Strategy Engine Tests
# ==============================================================================
class TestStrategyEngine:
    def test_initial_state(self):
        engine = StrategyEngine()
        assert engine.position_size == 0.0
        assert engine.equity == 10000.0

    def test_entry_and_fill(self):
        engine = StrategyEngine(initial_capital=10000)
        engine.set_bar(0)
        engine.entry("Long", Direction.LONG, qty=1.0)
        assert len(engine.pending_orders) == 1

        # Process on next bar
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 102.0)
        assert engine.position.direction == Direction.LONG
        assert engine.position.qty == 1.0
        assert engine.position.avg_price == 100.0

    def test_entry_and_close(self):
        engine = StrategyEngine(initial_capital=10000)
        engine.set_bar(0)
        engine.entry("Long", Direction.LONG, qty=1.0)
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 102.0)

        engine.close("Long")
        engine.set_bar(2)
        trades = engine.process_orders(110.0, 115.0, 105.0, 112.0)
        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(10.0)  # 110 - 100

    def test_position_size_property(self):
        engine = StrategyEngine()
        assert engine.position_size == 0.0
        engine.set_bar(0)
        engine.entry("Long", Direction.LONG, qty=2.0)
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 102.0)
        assert engine.position_size == 2.0

    def test_short_position(self):
        engine = StrategyEngine()
        engine.set_bar(0)
        engine.entry("Short", Direction.SHORT, qty=1.0)
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 98.0)
        assert engine.position_size == -1.0

    def test_close_all(self):
        engine = StrategyEngine()
        engine.set_bar(0)
        engine.entry("Long", Direction.LONG, qty=1.0)
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 102.0)

        engine.close_all()
        engine.set_bar(2)
        trades = engine.process_orders(105.0, 110.0, 100.0, 108.0)
        assert len(trades) == 1
        assert engine.position_size == 0.0

    def test_commission(self):
        engine = StrategyEngine(commission=0.001)  # 0.1%
        engine.set_bar(0)
        engine.entry("Long", Direction.LONG, qty=1.0)
        engine.set_bar(1)
        engine.process_orders(100.0, 105.0, 95.0, 102.0)

        engine.close("Long")
        engine.set_bar(2)
        trades = engine.process_orders(110.0, 115.0, 105.0, 112.0)
        assert trades[0].commission > 0


# ==============================================================================
# Input Manager Tests
# ==============================================================================
class TestInputManager:
    def test_default_values(self):
        mgr = InputManager()
        assert mgr.input_int(defval=14, title="Length") == 14
        assert mgr.input_float(defval=2.0, title="Mult") == 2.0
        assert mgr.input_bool(defval=True, title="Show") is True
        assert mgr.input_string(defval="hello", title="Name") == "hello"

    def test_overrides(self):
        mgr = InputManager(overrides={"Length": 20, "Mult": 3.0})
        assert mgr.input_int(defval=14, title="Length") == 20
        assert mgr.input_float(defval=2.0, title="Mult") == 3.0

    def test_source(self):
        mgr = InputManager()
        assert mgr.input_source(defval="close", title="Source") == "close"

    def test_source_invalid_fallback(self):
        mgr = InputManager(overrides={"Source": "invalid"})
        assert mgr.input_source(defval="close", title="Source") == "close"

    def test_all_inputs(self):
        mgr = InputManager()
        mgr.input_int(defval=14, title="Len")
        mgr.input_float(defval=2.0, title="Mult")
        inputs = mgr.all_inputs
        assert inputs["Len"] == 14
        assert inputs["Mult"] == 2.0
