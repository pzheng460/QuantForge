"""Parity tests: streaming indicators vs vectorized numpy implementations.

Verifies that StreamingEMA, StreamingATR, StreamingROC, StreamingSMA, StreamingADX
produce values identical (within floating-point tolerance) to the vectorized
calculate_xxx() functions in strategy/strategies/momentum/core.py.
"""

import numpy as np
import pytest

from strategy.indicators.base import (
    StreamingADX,
    StreamingATR,
    StreamingEMA,
    StreamingROC,
    StreamingSMA,
)
from strategy.strategies.momentum.core import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_roc,
    calculate_sma,
)

# Tolerance for floating-point comparison
ATOL = 1e-10


def _generate_ohlcv(n: int = 1000, seed: int = 42):
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    # Random walk for close prices
    returns = rng.normal(0.0001, 0.02, n)
    close = 100.0 * np.exp(np.cumsum(returns))

    # High/low around close
    spread = rng.uniform(0.005, 0.03, n) * close
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)

    # Volume
    volume = rng.uniform(100, 10000, n)

    return close, high, low, volume


class TestStreamingEMAParity:
    """Test StreamingEMA matches calculate_ema."""

    @pytest.mark.parametrize("period", [5, 8, 14, 21, 50])
    def test_ema_parity(self, period):
        close, _, _, _ = _generate_ohlcv(500)

        # Vectorized
        vec_ema = calculate_ema(close, period)

        # Streaming
        stream = StreamingEMA(period)
        stream_vals = []
        for price in close:
            val = stream.update(price)
            stream_vals.append(val)

        # Compare at indices where vectorized has valid values
        for i in range(len(close)):
            if np.isnan(vec_ema[i]):
                assert stream_vals[i] is None, (
                    f"Index {i}: expected None, got {stream_vals[i]}"
                )
            else:
                assert stream_vals[i] is not None, (
                    f"Index {i}: expected value, got None"
                )
                assert abs(stream_vals[i] - vec_ema[i]) < ATOL, (
                    f"Index {i}: streaming={stream_vals[i]}, vectorized={vec_ema[i]}, "
                    f"diff={abs(stream_vals[i] - vec_ema[i])}"
                )

    def test_ema_reset(self):
        close, _, _, _ = _generate_ohlcv(100)
        stream = StreamingEMA(10)
        for price in close:
            stream.update(price)
        assert stream.value is not None
        stream.reset()
        assert stream.value is None


class TestStreamingATRParity:
    """Test StreamingATR matches calculate_atr."""

    @pytest.mark.parametrize("period", [7, 14, 21])
    def test_atr_parity(self, period):
        close, high, low, _ = _generate_ohlcv(500)

        # Vectorized
        vec_atr = calculate_atr(high, low, close, period)

        # Streaming
        stream = StreamingATR(period)
        stream_vals = []
        for i in range(len(close)):
            val = stream.update(high[i], low[i], close[i])
            stream_vals.append(val)

        # Compare
        for i in range(len(close)):
            if np.isnan(vec_atr[i]):
                assert stream_vals[i] is None, (
                    f"Index {i}: expected None, got {stream_vals[i]}"
                )
            else:
                assert stream_vals[i] is not None, (
                    f"Index {i}: expected value, got None"
                )
                assert abs(stream_vals[i] - vec_atr[i]) < ATOL, (
                    f"Index {i}: streaming={stream_vals[i]}, vectorized={vec_atr[i]}, "
                    f"diff={abs(stream_vals[i] - vec_atr[i])}"
                )


class TestStreamingROCParity:
    """Test StreamingROC matches calculate_roc."""

    @pytest.mark.parametrize("period", [5, 14, 21])
    def test_roc_parity(self, period):
        close, _, _, _ = _generate_ohlcv(500)

        # Vectorized
        vec_roc = calculate_roc(close, period)

        # Streaming
        stream = StreamingROC(period)
        stream_vals = []
        for price in close:
            val = stream.update(price)
            stream_vals.append(val)

        # Compare
        for i in range(len(close)):
            if np.isnan(vec_roc[i]):
                assert stream_vals[i] is None, (
                    f"Index {i}: expected None, got {stream_vals[i]}"
                )
            else:
                assert stream_vals[i] is not None, (
                    f"Index {i}: expected value, got None"
                )
                assert abs(stream_vals[i] - vec_roc[i]) < ATOL, (
                    f"Index {i}: streaming={stream_vals[i]}, vectorized={vec_roc[i]}, "
                    f"diff={abs(stream_vals[i] - vec_roc[i])}"
                )


class TestStreamingSMAParity:
    """Test StreamingSMA matches calculate_sma."""

    @pytest.mark.parametrize("period", [5, 10, 20])
    def test_sma_parity(self, period):
        close, _, _, _ = _generate_ohlcv(500)

        # Vectorized
        vec_sma = calculate_sma(close, period)

        # Streaming
        stream = StreamingSMA(period)
        stream_vals = []
        for price in close:
            val = stream.update(price)
            stream_vals.append(val)

        # Compare
        for i in range(len(close)):
            if np.isnan(vec_sma[i]):
                assert stream_vals[i] is None, (
                    f"Index {i}: expected None, got {stream_vals[i]}"
                )
            else:
                assert stream_vals[i] is not None, (
                    f"Index {i}: expected value, got None"
                )
                assert abs(stream_vals[i] - vec_sma[i]) < ATOL, (
                    f"Index {i}: streaming={stream_vals[i]}, vectorized={vec_sma[i]}, "
                    f"diff={abs(stream_vals[i] - vec_sma[i])}"
                )


class TestStreamingADXParity:
    """Test StreamingADX matches calculate_adx."""

    @pytest.mark.parametrize("period", [7, 14, 21])
    def test_adx_parity(self, period):
        close, high, low, _ = _generate_ohlcv(500)

        # Vectorized
        vec_adx = calculate_adx(high, low, close, period)

        # Streaming
        stream = StreamingADX(period)
        stream_vals = []
        for i in range(len(close)):
            val = stream.update(high[i], low[i], close[i])
            stream_vals.append(val)

        # Compare
        for i in range(len(close)):
            if np.isnan(vec_adx[i]):
                assert stream_vals[i] is None, (
                    f"Index {i}: expected None, got {stream_vals[i]}"
                )
            else:
                assert stream_vals[i] is not None, (
                    f"Index {i}: expected value, got None (vec={vec_adx[i]})"
                )
                assert abs(stream_vals[i] - vec_adx[i]) < ATOL, (
                    f"Index {i}: streaming={stream_vals[i]}, vectorized={vec_adx[i]}, "
                    f"diff={abs(stream_vals[i] - vec_adx[i])}"
                )

    def test_adx_needs_enough_bars(self):
        """ADX requires 2*period+1 bars minimum for a valid value."""
        period = 14
        stream = StreamingADX(period)
        close, high, low, _ = _generate_ohlcv(50)
        for i in range(2 * period):
            val = stream.update(high[i], low[i], close[i])
            assert val is None, f"ADX should not be ready at bar {i}"
