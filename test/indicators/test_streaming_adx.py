"""StreamingADX numerical parity vs a reference Wilder ADX implementation.

Regression: the streaming ADX seeded at ``period+1`` DX values instead of
``period``, which delayed readiness by one bar AND permanently dropped one DX
sample from Wilder smoothing. This proves the fixed implementation matches a
reference Wilder ADX on realistic synthetic data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from quantforge.indicators.streaming import StreamingADX


@dataclass
class _Candle:
    high: float
    low: float
    close: float


def _reference_adx(candles: list[_Candle], period: int) -> list[float | None]:
    """Standard Wilder ADX.

    Returns one value per bar; None until the first (period) seeded DX values
    are available (ready at bar 2*period - 1 in 1-based terms).
    """
    out: list[float | None] = [None] * len(candles)
    if len(candles) < period + 1:
        return out

    prev_high = candles[0].high
    prev_low = candles[0].low
    prev_close = candles[0].close
    trs: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []
    dx_values: list[float] = []
    atr_s: float | None = None
    plus_s: float | None = None
    minus_s: float | None = None
    adx: float | None = None

    for i in range(1, len(candles)):
        c = candles[i]
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        plus_dm = c.high - prev_high if c.high - prev_high > prev_low - c.low else 0.0
        minus_dm = prev_low - c.low if prev_low - c.low > c.high - prev_high else 0.0
        plus_dm = max(0.0, plus_dm)
        minus_dm = max(0.0, minus_dm)
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        if len(trs) == period:
            # SUM-based seed, matching the streaming sum-based Wilder
            # smoothing (the seed and the recursion must use the SAME scale —
            # mixing a mean seed with sum recursion corrupts every DX).
            atr_s = sum(trs)
            plus_s = sum(plus_dms)
            minus_s = sum(minus_dms)
        elif len(trs) > period:
            atr_s = atr_s - atr_s / period + tr
            plus_s = plus_s - plus_s / period + plus_dm
            minus_s = minus_s - minus_s / period + minus_dm
        if atr_s and atr_s > 0 and plus_s is not None and minus_s is not None:
            plus_di = 100.0 * plus_s / atr_s
            minus_di = 100.0 * minus_s / atr_s
            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            dx_values.append(dx)
            if adx is None and len(dx_values) >= period:
                adx = sum(dx_values[:period]) / period
            elif adx is not None:
                adx = (adx * (period - 1) + dx) / period
        prev_high = c.high
        prev_low = c.low
        prev_close = c.close
        out[i] = adx
    return out


@pytest.mark.parametrize("period", [3, 5, 14])
def test_streaming_adx_matches_reference_wilder(period: int):
    rng = random.Random(42)
    candles: list[_Candle] = []
    price = 100.0
    for i in range(240):
        drift = rng.uniform(-1.0, 1.0) if i % 12 < 11 else rng.uniform(-3.0, 3.0)
        price += drift
        high = price + rng.uniform(0.1, 1.5)
        low = price - rng.uniform(0.1, 1.5)
        candles.append(_Candle(high=max(high, price), low=min(low, price), close=price))

    reference = _reference_adx(candles, period)

    adx = StreamingADX(period)
    streamed: list[float | None] = []
    for c in candles:
        streamed.append(adx.update(c.high, c.low, c.close))

    # Readiness alignment: both must become non-None on the same bar.
    def first_ready(values: list[float | None]) -> int:
        for i, v in enumerate(values):
            if v is not None:
                return i
        return -1

    assert first_ready(streamed) == first_ready(reference)
    assert first_ready(streamed) == 2 * period - 1  # bar index (0-based)

    # Numerical parity from readiness onward.
    for i in range(first_ready(streamed), len(candles)):
        assert streamed[i] == pytest.approx(reference[i], rel=1e-9, abs=1e-9)


def test_streaming_adx_stays_none_until_enough_data():
    adx = StreamingADX(3)
    bars = [(100.0, 99.0, 99.5)] * 8
    values = [adx.update(h, lo, c) for h, lo, c in bars]
    # Ready at bar 2*3 - 1 = 5 (0-based), i.e. values[5] is the first.
    assert values[:5] == [None] * 5
    assert values[5] is not None


def test_streaming_adx_reset_clears_state():
    adx = StreamingADX(3)
    for h, lo, c in [(100.0, 99.0, 99.5)] * 10:
        adx.update(h, lo, c)
    assert adx.value is not None
    adx.reset()
    assert adx.value is None
