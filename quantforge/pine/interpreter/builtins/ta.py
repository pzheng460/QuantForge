"""Technical analysis builtins: ta.sma, ta.ema, ta.rsi, ta.atr, ta.adx, etc.

All formulas match TradingView exactly.
"""

from __future__ import annotations

import math
from collections import deque

from quantforge.pine.interpreter.series import PineSeries, is_na


# ---------------------------------------------------------------------------
# Internal smoothing helpers
# ---------------------------------------------------------------------------


def _rma_step(prev: float | None, value: float, length: int) -> float | None:
    """Single step of RMA (Wilder smoothing): rma = (prev * (length-1) + value) / length.

    This is the same as TradingView's ta.rma / Wilder's smoothing method.
    """
    if prev is None or is_na(prev):
        return None
    return (prev * (length - 1) + value) / length


# ---------------------------------------------------------------------------
# Stateful TA calculators (one per series)
# ---------------------------------------------------------------------------


class _SMACalc:
    """Rolling SMA calculator."""

    def __init__(self, length: int):
        self.length = length
        self._window: deque[float] = deque(maxlen=length)
        self._values: list[float | None] = []

    def update(self, value: float) -> float | None:
        if is_na(value) or value is None:
            self._values.append(None)
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            self._values.append(None)
            return None
        result = sum(self._window) / self.length
        self._values.append(result)
        return result


class _EMACalc:
    """EMA: alpha = 2/(length+1), seed with SMA of first `length` bars."""

    def __init__(self, length: int):
        self.length = length
        self._alpha = 2.0 / (length + 1)
        self._sum = 0.0
        self._count = 0
        self._prev: float | None = None
        self._values: list[float | None] = []

    def update(self, value: float) -> float | None:
        if is_na(value) or value is None:
            self._values.append(None)
            return None

        if self._prev is None:
            # Accumulate for SMA seed
            self._sum += value
            self._count += 1
            if self._count < self.length:
                self._values.append(None)
                return None
            # Seed with SMA
            self._prev = self._sum / self.length
            self._values.append(self._prev)
            return self._prev

        self._prev = self._alpha * value + (1 - self._alpha) * self._prev
        self._values.append(self._prev)
        return self._prev


class _RMACalc:
    """RMA (Wilder smoothing): rma = (prev*(length-1) + value) / length.

    Seed: SMA of first `length` bars.
    """

    def __init__(self, length: int):
        self.length = length
        self._sum = 0.0
        self._count = 0
        self._prev: float | None = None
        self._values: list[float | None] = []

    def update(self, value: float) -> float | None:
        if is_na(value) or value is None:
            self._values.append(None)
            return None

        if self._prev is None:
            self._sum += value
            self._count += 1
            if self._count < self.length:
                self._values.append(None)
                return None
            self._prev = self._sum / self.length
            self._values.append(self._prev)
            return self._prev

        self._prev = (self._prev * (self.length - 1) + value) / self.length
        self._values.append(self._prev)
        return self._prev


class _RSICalc:
    """RSI using RMA (Wilder smoothing) for avg gain / avg loss."""

    def __init__(self, length: int):
        self.length = length
        self._prev_close: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._values: list[float | None] = []
        self._count = 0

    def update(self, close: float) -> float | None:
        if is_na(close) or close is None:
            self._values.append(None)
            return None

        if self._prev_close is None:
            self._prev_close = close
            self._values.append(None)
            return None

        change = close - self._prev_close
        self._prev_close = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._count += 1

        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if self._count < self.length:
                self._values.append(None)
                return None
            # Seed with SMA
            self._avg_gain = sum(self._gains) / self.length
            self._avg_loss = sum(self._losses) / self.length
        else:
            # Wilder smoothing (RMA)
            self._avg_gain = (self._avg_gain * (self.length - 1) + gain) / self.length
            self._avg_loss = (self._avg_loss * (self.length - 1) + loss) / self.length

        if self._avg_loss == 0:
            result = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            result = 100.0 - 100.0 / (1.0 + rs)
        self._values.append(result)
        return result


class _ATRCalc:
    """ATR using RMA (Wilder smoothing) of True Range."""

    def __init__(self, length: int):
        self.length = length
        self._rma = _RMACalc(length)
        self._prev_close: float | None = None
        self._values: list[float | None] = []

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low, abs(high - self._prev_close), abs(low - self._prev_close)
            )
        self._prev_close = close
        result = self._rma.update(tr)
        self._values.append(result)
        return result


class _ADXCalc:
    """ADX using Wilder smoothing (RMA).

    Matches TradingView's ta.adx(len):
    1. +DM / -DM
    2. Smooth with RMA(len)
    3. +DI / -DI = 100 * smoothed_DM / ATR
    4. DX = 100 * |+DI - -DI| / (+DI + -DI)
    5. ADX = RMA(DX, len)
    """

    def __init__(self, length: int):
        self.length = length
        self._atr = _ATRCalc(length)
        self._plus_dm_rma = _RMACalc(length)
        self._minus_dm_rma = _RMACalc(length)
        self._adx_rma = _RMACalc(length)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._values: list[float | None] = []

    def update(self, high: float, low: float, close: float) -> float | None:
        atr = self._atr.update(high, low, close)

        if self._prev_high is None:
            self._prev_high = high
            self._prev_low = low
            self._values.append(None)
            return None

        up_move = high - self._prev_high
        down_move = self._prev_low - low
        self._prev_high = high
        self._prev_low = low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        smooth_plus = self._plus_dm_rma.update(plus_dm)
        smooth_minus = self._minus_dm_rma.update(minus_dm)

        if atr is None or smooth_plus is None or smooth_minus is None or atr == 0:
            self._values.append(None)
            return None

        plus_di = 100.0 * smooth_plus / atr
        minus_di = 100.0 * smooth_minus / atr

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx = 0.0
        else:
            dx = 100.0 * abs(plus_di - minus_di) / di_sum

        adx = self._adx_rma.update(dx)
        self._values.append(adx)
        return adx


class _MACDCalc:
    """MACD: fast EMA - slow EMA, signal = EMA of MACD line."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast_ema = _EMACalc(fast)
        self._slow_ema = _EMACalc(slow)
        self._signal_ema = _EMACalc(signal)
        self._macd_values: list[float | None] = []
        self._signal_values: list[float | None] = []
        self._hist_values: list[float | None] = []

    def update(self, close: float) -> tuple[float | None, float | None, float | None]:
        fast = self._fast_ema.update(close)
        slow = self._slow_ema.update(close)

        if fast is None or slow is None:
            self._macd_values.append(None)
            self._signal_values.append(None)
            self._hist_values.append(None)
            return None, None, None

        macd_line = fast - slow
        self._macd_values.append(macd_line)

        signal = self._signal_ema.update(macd_line)
        self._signal_values.append(signal)

        if signal is None:
            self._hist_values.append(None)
            return macd_line, None, None

        hist = macd_line - signal
        self._hist_values.append(hist)
        return macd_line, signal, hist


class _BBCalc:
    """Bollinger Bands: middle = SMA, upper/lower = middle ± mult*stdev."""

    def __init__(self, length: int, mult: float = 2.0):
        self.length = length
        self.mult = mult
        self._window: deque[float] = deque(maxlen=length)

    def update(self, close: float) -> tuple[float | None, float | None, float | None]:
        self._window.append(close)
        if len(self._window) < self.length:
            return None, None, None
        middle = sum(self._window) / self.length
        variance = sum((x - middle) ** 2 for x in self._window) / self.length
        std = math.sqrt(variance)
        upper = middle + self.mult * std
        lower = middle - self.mult * std
        return upper, middle, lower


class _StochCalc:
    """Stochastic: %K = 100 * (close - lowest_low) / (highest_high - lowest_low), %D = SMA(%K)."""

    def __init__(self, k_length: int = 14, k_smooth: int = 1, d_smooth: int = 3):
        self.k_length = k_length
        self._highs: deque[float] = deque(maxlen=k_length)
        self._lows: deque[float] = deque(maxlen=k_length)
        self._k_sma = _SMACalc(k_smooth) if k_smooth > 1 else None
        self._d_sma = _SMACalc(d_smooth)

    def update(
        self, high: float, low: float, close: float
    ) -> tuple[float | None, float | None]:
        self._highs.append(high)
        self._lows.append(low)
        if len(self._highs) < self.k_length:
            return None, None
        hh = max(self._highs)
        ll = min(self._lows)
        if hh == ll:
            raw_k = 50.0
        else:
            raw_k = 100.0 * (close - ll) / (hh - ll)
        k = self._k_sma.update(raw_k) if self._k_sma else raw_k
        if k is None:
            return None, None
        d = self._d_sma.update(k)
        return k, d


# ---------------------------------------------------------------------------
# Registry: manage calculator instances per (context, key)
# ---------------------------------------------------------------------------

_calculators: dict[int, dict[str, object]] = {}


def _get_calc(ctx_id: int, key: str, factory):
    """Get or create a calculator for a given context and key."""
    if ctx_id not in _calculators:
        _calculators[ctx_id] = {}
    calcs = _calculators[ctx_id]
    if key not in calcs:
        calcs[key] = factory()
    return calcs[key]


def reset_calculators(ctx_id: int | None = None) -> None:
    """Reset calculator state. If ctx_id is None, reset all."""
    if ctx_id is None:
        _calculators.clear()
    elif ctx_id in _calculators:
        del _calculators[ctx_id]


# ---------------------------------------------------------------------------
# Public ta.* functions
# ---------------------------------------------------------------------------


def ta_sma(ctx, source_series: PineSeries, length: int) -> float | None:
    key = f"sma_{id(source_series)}_{length}"
    calc = _get_calc(id(ctx), key, lambda: _SMACalc(length))
    val = source_series.current
    if val is None:
        return None
    return calc.update(val)


def ta_ema(ctx, source_series: PineSeries, length: int) -> float | None:
    key = f"ema_{id(source_series)}_{length}"
    calc = _get_calc(id(ctx), key, lambda: _EMACalc(length))
    val = source_series.current
    if val is None:
        return None
    return calc.update(val)


def ta_rma(ctx, source_series: PineSeries, length: int) -> float | None:
    key = f"rma_{id(source_series)}_{length}"
    calc = _get_calc(id(ctx), key, lambda: _RMACalc(length))
    val = source_series.current
    if val is None:
        return None
    return calc.update(val)


def ta_rsi(ctx, source_series: PineSeries, length: int) -> float | None:
    key = f"rsi_{id(source_series)}_{length}"
    calc = _get_calc(id(ctx), key, lambda: _RSICalc(length))
    val = source_series.current
    if val is None:
        return None
    return calc.update(val)


def ta_atr(ctx, length: int) -> float | None:
    key = f"atr_{length}"
    calc = _get_calc(id(ctx), key, lambda: _ATRCalc(length))
    high_s = ctx.get_series("high")
    low_s = ctx.get_series("low")
    close_s = ctx.get_series("close")
    hi, lo, cl = high_s.current, low_s.current, close_s.current
    if hi is None or lo is None or cl is None:
        return None
    return calc.update(hi, lo, cl)


def ta_adx(ctx, length: int) -> float | None:
    key = f"adx_{length}"
    calc = _get_calc(id(ctx), key, lambda: _ADXCalc(length))
    high_s = ctx.get_series("high")
    low_s = ctx.get_series("low")
    close_s = ctx.get_series("close")
    hi, lo, cl = high_s.current, low_s.current, close_s.current
    if hi is None or lo is None or cl is None:
        return None
    return calc.update(hi, lo, cl)


def ta_macd(
    ctx, source_series: PineSeries, fast: int = 12, slow: int = 26, signal: int = 9
):
    """Returns (macd_line, signal_line, histogram)."""
    key = f"macd_{id(source_series)}_{fast}_{slow}_{signal}"
    calc = _get_calc(id(ctx), key, lambda: _MACDCalc(fast, slow, signal))
    val = source_series.current
    if val is None:
        return None, None, None
    return calc.update(val)


def ta_bbands(ctx, source_series: PineSeries, length: int = 20, mult: float = 2.0):
    """Returns (upper, middle, lower)."""
    key = f"bb_{id(source_series)}_{length}_{mult}"
    calc = _get_calc(id(ctx), key, lambda: _BBCalc(length, mult))
    val = source_series.current
    if val is None:
        return None, None, None
    return calc.update(val)


def ta_stoch(ctx, k_length: int = 14, k_smooth: int = 1, d_smooth: int = 3):
    """Returns (k, d)."""
    key = f"stoch_{k_length}_{k_smooth}_{d_smooth}"
    calc = _get_calc(id(ctx), key, lambda: _StochCalc(k_length, k_smooth, d_smooth))
    high_s = ctx.get_series("high")
    low_s = ctx.get_series("low")
    close_s = ctx.get_series("close")
    hi, lo, cl = high_s.current, low_s.current, close_s.current
    if hi is None or lo is None or cl is None:
        return None, None
    return calc.update(hi, lo, cl)


def ta_crossover(series_a: PineSeries, series_b: PineSeries) -> bool:
    """True when series_a crosses above series_b."""
    a0, a1 = series_a[0], series_a[1]
    b0, b1 = series_b[0], series_b[1]
    if any(is_na(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 > b0 and a1 <= b1


def ta_crossunder(series_a: PineSeries, series_b: PineSeries) -> bool:
    """True when series_a crosses below series_b."""
    a0, a1 = series_a[0], series_a[1]
    b0, b1 = series_b[0], series_b[1]
    if any(is_na(v) for v in (a0, a1, b0, b1)):
        return False
    return a0 < b0 and a1 >= b1


def ta_highest(source_series: PineSeries, length: int) -> float | None:
    """Highest value in the last `length` bars."""
    vals = []
    for i in range(length):
        v = source_series[i]
        if is_na(v):
            return None
        vals.append(v)
    return max(vals)


def ta_lowest(source_series: PineSeries, length: int) -> float | None:
    """Lowest value in the last `length` bars."""
    vals = []
    for i in range(length):
        v = source_series[i]
        if is_na(v):
            return None
        vals.append(v)
    return max(vals) if not vals else min(vals)


def ta_change(source_series: PineSeries, length: int = 1) -> float | None:
    """Change in value over `length` bars: series[0] - series[length]."""
    cur = source_series[0]
    prev = source_series[length]
    if is_na(cur) or is_na(prev):
        return None
    return cur - prev


def ta_stdev(ctx, source_series: PineSeries, length: int) -> float | None:
    """Population standard deviation over `length` bars (matches TradingView ta.stdev)."""
    key = f"stdev_{id(source_series)}_{length}"
    calc = _get_calc(id(ctx), key, lambda: _StdevCalc(length))
    val = source_series.current
    if val is None:
        return None
    return calc.update(val)


class _StdevCalc:
    """Rolling population standard deviation."""

    def __init__(self, length: int):
        self.length = length
        self._window: deque[float] = deque(maxlen=length)

    def update(self, value: float) -> float | None:
        if is_na(value) or value is None:
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            return None
        mean = sum(self._window) / self.length
        variance = sum((x - mean) ** 2 for x in self._window) / self.length
        return math.sqrt(variance)


def ta_tr(ctx) -> float | None:
    """True range for current bar."""
    high_s = ctx.get_series("high")
    low_s = ctx.get_series("low")
    close_s = ctx.get_series("close")
    hi, lo = high_s.current, low_s.current
    prev_c = close_s[1]
    if hi is None or lo is None:
        return None
    if prev_c is None:
        return hi - lo
    return max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
