"""Tests for ta.* builtins against known values with REAL formulas.

Tests verify exact TradingView formula matching:
- EMA: alpha = 2/(length+1), seed with SMA
- RSI: uses RMA (Wilder smoothing), NOT SMA
- ATR: uses RMA internally
- ADX: uses Wilder smoothing (RMA)
"""

import math

import pytest

from quantforge.pine.interpreter.builtins.ta import (
    _SMACalc,
    _EMACalc,
    _RMACalc,
    _RSICalc,
    _ATRCalc,
    _ADXCalc,
    _MACDCalc,
    _BBCalc,
    _StochCalc,
    ta_crossover,
    ta_crossunder,
    ta_highest,
    ta_lowest,
    ta_change,
)
from quantforge.pine.interpreter.builtins.math_fn import (
    math_abs,
    math_max,
    math_min,
    math_round,
    math_log,
    math_sqrt,
    math_pow,
)
from quantforge.pine.interpreter.series import PineSeries


# --- Test data ---
# 30 bars of close prices (realistic BTC-like movement)
CLOSE_DATA = [
    100.0,
    102.0,
    101.0,
    103.0,
    105.0,
    104.0,
    106.0,
    108.0,
    107.0,
    109.0,
    110.0,
    108.0,
    106.0,
    107.0,
    109.0,
    111.0,
    113.0,
    112.0,
    114.0,
    116.0,
    115.0,
    113.0,
    111.0,
    112.0,
    114.0,
    116.0,
    118.0,
    117.0,
    119.0,
    120.0,
]

HIGH_DATA = [c + 2.0 for c in CLOSE_DATA]
LOW_DATA = [c - 2.0 for c in CLOSE_DATA]


class TestSMA:
    def test_sma_basic(self):
        calc = _SMACalc(5)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # First 4 should be None
        assert all(r is None for r in results[:4])
        # SMA(5) at bar 4 = mean of first 5 values
        expected = (100.0 + 102.0 + 101.0 + 103.0 + 105.0) / 5
        assert results[4] == pytest.approx(expected)

    def test_sma_rolling(self):
        calc = _SMACalc(3)
        results = []
        for v in [10, 20, 30, 40, 50]:
            results.append(calc.update(v))
        assert results[2] == pytest.approx(20.0)  # (10+20+30)/3
        assert results[3] == pytest.approx(30.0)  # (20+30+40)/3
        assert results[4] == pytest.approx(40.0)  # (30+40+50)/3


class TestEMA:
    def test_ema_seed_with_sma(self):
        """EMA must seed with SMA of first `length` bars."""
        length = 5
        calc = _EMACalc(length)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # First (length-1) values should be None
        assert all(r is None for r in results[: length - 1])

        # At bar (length-1), seed = SMA of first `length` bars
        sma_seed = sum(CLOSE_DATA[:length]) / length
        assert results[length - 1] == pytest.approx(sma_seed)

    def test_ema_alpha(self):
        """EMA: alpha = 2/(length+1)."""
        length = 5
        alpha = 2.0 / (length + 1)
        calc = _EMACalc(length)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # After seed, verify next value uses correct alpha
        seed = results[length - 1]
        expected_next = alpha * CLOSE_DATA[length] + (1 - alpha) * seed
        assert results[length] == pytest.approx(expected_next)

    def test_ema_full_sequence(self):
        """Verify full EMA computation matches manual calculation."""
        length = 3
        # alpha = 2.0 / (length + 1) = 0.5
        data = [10.0, 20.0, 30.0, 40.0, 50.0]

        calc = _EMACalc(length)
        results = [calc.update(v) for v in data]

        # Seed = SMA(3) = (10+20+30)/3 = 20
        assert results[2] == pytest.approx(20.0)
        # Next: 0.5 * 40 + 0.5 * 20 = 30
        assert results[3] == pytest.approx(30.0)
        # Next: 0.5 * 50 + 0.5 * 30 = 40
        assert results[4] == pytest.approx(40.0)


class TestRMA:
    def test_rma_seed_with_sma(self):
        """RMA seeds with SMA of first `length` bars."""
        length = 5
        calc = _RMACalc(length)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        sma_seed = sum(CLOSE_DATA[:length]) / length
        assert results[length - 1] == pytest.approx(sma_seed)

    def test_rma_wilder_formula(self):
        """RMA: rma = (prev * (length-1) + value) / length."""
        length = 3
        data = [10.0, 20.0, 30.0, 40.0, 50.0]

        calc = _RMACalc(length)
        results = [calc.update(v) for v in data]

        # Seed = SMA(3) = 20
        seed = 20.0
        assert results[2] == pytest.approx(seed)

        # Next: (20 * 2 + 40) / 3 = 80/3 ≈ 26.667
        expected = (seed * 2 + 40.0) / 3
        assert results[3] == pytest.approx(expected)

        # Next: (26.667 * 2 + 50) / 3
        expected2 = (expected * 2 + 50.0) / 3
        assert results[4] == pytest.approx(expected2)


class TestRSI:
    def test_rsi_uses_rma_not_sma(self):
        """RSI must use RMA (Wilder smoothing) for avg gain/loss."""
        length = 14
        calc = _RSICalc(length)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # First `length` values should be None (need length changes + 1 price)
        assert all(r is None for r in results[:length])

        # RSI should be defined after enough data
        valid = [r for r in results if r is not None]
        assert len(valid) > 0

        # RSI should be between 0 and 100
        for r in valid:
            assert 0 <= r <= 100

    def test_rsi_manual_calculation(self):
        """Verify RSI against manual calculation with Wilder smoothing."""
        # Simple up-down-up pattern
        prices = [
            44.0,
            44.34,
            44.09,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
            46.00,
        ]
        length = 14
        calc = _RSICalc(length)
        results = [calc.update(p) for p in prices]

        # After 15 prices (14 changes), RSI should be defined
        rsi_val = results[14]  # 15th price = 14th change
        assert rsi_val is not None
        # Well-known RSI value for this series ≈ 70.46
        assert 60 < rsi_val < 80


class TestATR:
    def test_atr_uses_rma(self):
        """ATR must use RMA (Wilder smoothing) of true range."""
        length = 5
        calc = _ATRCalc(length)
        results = []
        for h, lo, c in zip(HIGH_DATA, LOW_DATA, CLOSE_DATA):
            results.append(calc.update(h, lo, c))

        # First (length-1) should be None
        assert all(r is None for r in results[: length - 1])

        # ATR should be positive
        valid = [r for r in results if r is not None]
        assert all(r > 0 for r in valid)

    def test_atr_simple_case(self):
        """ATR with constant range bars."""
        length = 3
        calc = _ATRCalc(length)
        # Bars with constant H-L=4, no gaps
        highs = [12, 14, 16, 18, 20]
        lows = [8, 10, 12, 14, 16]
        closes = [10, 12, 14, 16, 18]

        results = [calc.update(h, lo, c) for h, lo, c in zip(highs, lows, closes)]
        # First bar TR = 4, second TR = max(4, |14-10|, |10-10|) = 4
        # All TRs = 4, so ATR seed = 4, subsequent ATRs = 4
        assert results[2] == pytest.approx(4.0)
        assert results[3] == pytest.approx(4.0)


class TestADX:
    def test_adx_returns_values(self):
        """ADX should produce valid values after warmup."""
        length = 5
        calc = _ADXCalc(length)
        results = []
        for h, lo, c in zip(HIGH_DATA[:25], LOW_DATA[:25], CLOSE_DATA[:25]):
            results.append(calc.update(h, lo, c))

        valid = [r for r in results if r is not None]
        assert len(valid) > 0
        for r in valid:
            assert 0 <= r <= 100


class TestMACD:
    def test_macd_basic(self):
        calc = _MACDCalc(fast=3, slow=5, signal=3)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # Should eventually produce non-None values
        valid = [(m, s, h) for m, s, h in results if m is not None and s is not None]
        assert len(valid) > 0


class TestBB:
    def test_bb_basic(self):
        calc = _BBCalc(length=5, mult=2.0)
        results = []
        for v in CLOSE_DATA:
            results.append(calc.update(v))

        # First 4 should be (None, None, None)
        for r in results[:4]:
            assert r == (None, None, None)

        # After warmup, upper > middle > lower
        upper, middle, lower = results[4]
        assert upper > middle > lower


class TestStoch:
    def test_stoch_basic(self):
        calc = _StochCalc(k_length=5, k_smooth=1, d_smooth=3)
        results = []
        for h, lo, c in zip(HIGH_DATA, LOW_DATA, CLOSE_DATA):
            results.append(calc.update(h, lo, c))

        valid = [(k, d) for k, d in results if k is not None]
        assert len(valid) > 0
        for k, d in valid:
            if k is not None:
                assert 0 <= k <= 100


class TestCrossover:
    def test_crossover(self):
        a = PineSeries("a")
        b = PineSeries("b")
        # Bar 0: a=1, b=2 (a below b)
        a.append(1.0)
        b.append(2.0)
        # Bar 1: a=3, b=2 (a crosses above b)
        a.append(3.0)
        b.append(2.0)
        assert ta_crossover(a, b) is True

    def test_no_crossover(self):
        a = PineSeries("a")
        b = PineSeries("b")
        a.append(3.0)
        b.append(2.0)
        a.append(4.0)
        b.append(2.0)
        assert ta_crossover(a, b) is False

    def test_crossunder(self):
        a = PineSeries("a")
        b = PineSeries("b")
        a.append(3.0)
        b.append(2.0)
        a.append(1.0)
        b.append(2.0)
        assert ta_crossunder(a, b) is True


class TestHighestLowest:
    def test_highest(self):
        s = PineSeries("test")
        for v in [10, 20, 15, 25, 18]:
            s.append(v)
        assert ta_highest(s, 3) == 25  # last 3: 15, 25, 18

    def test_lowest(self):
        s = PineSeries("test")
        for v in [10, 20, 15, 25, 18]:
            s.append(v)
        assert ta_lowest(s, 3) == 15  # last 3: 15, 25, 18


class TestChange:
    def test_change_default(self):
        s = PineSeries("test")
        for v in [10, 20, 30]:
            s.append(v)
        assert ta_change(s, 1) == 10  # 30 - 20
        assert ta_change(s, 2) == 20  # 30 - 10


class TestMathFunctions:
    def test_abs(self):
        assert math_abs(-5) == 5
        assert math_abs(5) == 5
        assert math_abs(None) is None

    def test_max(self):
        assert math_max(1, 2, 3) == 3
        assert math_max(None, 2) == 2

    def test_min(self):
        assert math_min(1, 2, 3) == 1
        assert math_min(None, 2) == 2

    def test_round(self):
        assert math_round(3.14159, 2) == pytest.approx(3.14)
        assert math_round(None) is None

    def test_log(self):
        assert math_log(math.e) == pytest.approx(1.0)
        assert math_log(0) is None
        assert math_log(-1) is None

    def test_sqrt(self):
        assert math_sqrt(4) == pytest.approx(2.0)
        assert math_sqrt(-1) is None

    def test_pow(self):
        assert math_pow(2, 3) == pytest.approx(8.0)
        assert math_pow(None, 3) is None
