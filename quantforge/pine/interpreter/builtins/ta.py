"""ta.* built-in functions — TradingView-exact implementations.

All functions operate on the interpreter's series and are called bar-by-bar.
They store their own state to accumulate values across bars.
"""

from __future__ import annotations

import math
from collections import deque


def _is_valid(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return True


# ==============================================================================
# SMA — Simple Moving Average
# ==============================================================================
class TaSMA:
    """ta.sma(source, length)"""

    def __init__(self, length: int):
        self.length = length
        self._buf: deque[float] = deque(maxlen=length)

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return None
        self._buf.append(value)
        if len(self._buf) < self.length:
            return None
        return sum(self._buf) / self.length


# ==============================================================================
# EMA — Exponential Moving Average (TV formula: alpha = 2/(length+1))
# ==============================================================================
class TaEMA:
    """ta.ema(source, length)"""

    def __init__(self, length: int):
        self.length = length
        self._alpha = 2.0 / (length + 1)
        self._prev: float | None = None
        self._count = 0

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return self._prev
        self._count += 1
        if self._prev is None:
            # TradingView seeds EMA with SMA of first `length` values
            self._prev = value
            return value
        self._prev = self._alpha * value + (1 - self._alpha) * self._prev
        return self._prev


# ==============================================================================
# RMA — Wilder Moving Average (used internally by RSI, ATR, ADX)
# TV formula: rma = alpha * source + (1 - alpha) * rma[1], alpha = 1/length
# Seeded with SMA of first `length` bars.
# ==============================================================================
class TaRMA:
    """ta.rma(source, length)"""

    def __init__(self, length: int):
        self.length = length
        self._alpha = 1.0 / length
        self._prev: float | None = None
        self._seed_buf: list[float] = []
        self._seeded = False

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return self._prev
        if not self._seeded:
            self._seed_buf.append(value)
            if len(self._seed_buf) >= self.length:
                self._prev = sum(self._seed_buf) / self.length
                self._seeded = True
                return self._prev
            return None
        self._prev = self._alpha * value + (1 - self._alpha) * self._prev
        return self._prev


# ==============================================================================
# RSI — Relative Strength Index (Wilder smoothing)
# ==============================================================================
class TaRSI:
    """ta.rsi(source, length)"""

    def __init__(self, length: int):
        self.length = length
        self._avg_gain = TaRMA(length)
        self._avg_loss = TaRMA(length)
        self._prev_val: float | None = None

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return None
        if self._prev_val is None:
            self._prev_val = value
            return None
        change = value - self._prev_val
        self._prev_val = value
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_g = self._avg_gain.update(gain)
        avg_l = self._avg_loss.update(loss)
        if avg_g is None or avg_l is None:
            return None
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100.0 - 100.0 / (1.0 + rs)


# ==============================================================================
# TR — True Range
# ==============================================================================
class TaTR:
    """ta.tr — needs high, low, previous close."""

    def __init__(self):
        self._prev_close: float | None = None

    def update(
        self, high: float | None, low: float | None, close: float | None
    ) -> float | None:
        if not (_is_valid(high) and _is_valid(low) and _is_valid(close)):
            return None
        if self._prev_close is None:
            self._prev_close = close
            return high - low
        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )
        self._prev_close = close
        return tr


# ==============================================================================
# ATR — Average True Range (RMA of TR)
# ==============================================================================
class TaATR:
    """ta.atr(length) = rma(tr, length)"""

    def __init__(self, length: int):
        self._tr = TaTR()
        self._rma = TaRMA(length)

    def update(
        self, high: float | None, low: float | None, close: float | None
    ) -> float | None:
        tr = self._tr.update(high, low, close)
        if tr is None:
            return None
        return self._rma.update(tr)


# ==============================================================================
# ADX — Average Directional Index (Wilder smoothing)
# ==============================================================================
class TaADX:
    """ta.adx(di_length, adx_length)"""

    def __init__(self, di_length: int, adx_length: int = 14):
        self.di_length = di_length
        self.adx_length = adx_length
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None
        self._plus_dm_rma = TaRMA(di_length)
        self._minus_dm_rma = TaRMA(di_length)
        self._tr_rma = TaRMA(di_length)
        self._adx_rma = TaRMA(adx_length)

    def update(
        self, high: float | None, low: float | None, close: float | None
    ) -> float | None:
        if not (_is_valid(high) and _is_valid(low) and _is_valid(close)):
            return None
        if self._prev_high is None:
            self._prev_high = high
            self._prev_low = low
            self._prev_close = close
            return None

        up_move = high - self._prev_high
        down_move = self._prev_low - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr = max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

        self._prev_high = high
        self._prev_low = low
        self._prev_close = close

        sm_plus = self._plus_dm_rma.update(plus_dm)
        sm_minus = self._minus_dm_rma.update(minus_dm)
        sm_tr = self._tr_rma.update(tr)

        if sm_plus is None or sm_minus is None or sm_tr is None or sm_tr == 0:
            return None

        plus_di = 100.0 * sm_plus / sm_tr
        minus_di = 100.0 * sm_minus / sm_tr
        dx_denom = plus_di + minus_di
        if dx_denom == 0:
            return None
        dx = 100.0 * abs(plus_di - minus_di) / dx_denom

        adx = self._adx_rma.update(dx)
        return adx


# ==============================================================================
# MACD
# ==============================================================================
class TaMACD:
    """ta.macd(source, fast, slow, signal) -> (macd_line, signal_line, histogram)"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast_ema = TaEMA(fast)
        self._slow_ema = TaEMA(slow)
        self._signal_ema = TaEMA(signal)

    def update(
        self, value: float | None
    ) -> tuple[float | None, float | None, float | None]:
        fast = self._fast_ema.update(value)
        slow = self._slow_ema.update(value)
        if fast is None or slow is None:
            return (None, None, None)
        macd_line = fast - slow
        signal_line = self._signal_ema.update(macd_line)
        if signal_line is None:
            return (macd_line, None, None)
        hist = macd_line - signal_line
        return (macd_line, signal_line, hist)


# ==============================================================================
# Bollinger Bands
# ==============================================================================
class TaBBands:
    """ta.bb(source, length, mult) -> (middle, upper, lower)"""

    def __init__(self, length: int, mult: float = 2.0):
        self.length = length
        self.mult = mult
        self._buf: deque[float] = deque(maxlen=length)

    def update(
        self, value: float | None
    ) -> tuple[float | None, float | None, float | None]:
        if not _is_valid(value):
            return (None, None, None)
        self._buf.append(value)
        if len(self._buf) < self.length:
            return (None, None, None)
        basis = sum(self._buf) / self.length
        variance = sum((x - basis) ** 2 for x in self._buf) / self.length
        std = math.sqrt(variance)
        upper = basis + self.mult * std
        lower = basis - self.mult * std
        return (basis, upper, lower)


# ==============================================================================
# Stochastic
# ==============================================================================
class TaStoch:
    """ta.stoch(close, high, low, length) -> %K value"""

    def __init__(self, length: int):
        self.length = length
        self._highs: deque[float] = deque(maxlen=length)
        self._lows: deque[float] = deque(maxlen=length)

    def update(
        self, close: float | None, high: float | None, low: float | None
    ) -> float | None:
        if not (_is_valid(close) and _is_valid(high) and _is_valid(low)):
            return None
        self._highs.append(high)
        self._lows.append(low)
        if len(self._highs) < self.length:
            return None
        hh = max(self._highs)
        ll = min(self._lows)
        if hh == ll:
            return 50.0
        return 100.0 * (close - ll) / (hh - ll)


# ==============================================================================
# Crossover / Crossunder
# ==============================================================================
class TaCrossover:
    """ta.crossover(a, b) — true when a crosses above b."""

    def __init__(self):
        self._prev_a: float | None = None
        self._prev_b: float | None = None

    def update(self, a: float | None, b: float | None) -> bool:
        if not (_is_valid(a) and _is_valid(b)):
            self._prev_a = a
            self._prev_b = b
            return False
        result = False
        if (
            _is_valid(self._prev_a)
            and _is_valid(self._prev_b)
            and self._prev_a <= self._prev_b
            and a > b
        ):
            result = True
        self._prev_a = a
        self._prev_b = b
        return result


class TaCrossunder:
    """ta.crossunder(a, b) — true when a crosses below b."""

    def __init__(self):
        self._prev_a: float | None = None
        self._prev_b: float | None = None

    def update(self, a: float | None, b: float | None) -> bool:
        if not (_is_valid(a) and _is_valid(b)):
            self._prev_a = a
            self._prev_b = b
            return False
        result = False
        if (
            _is_valid(self._prev_a)
            and _is_valid(self._prev_b)
            and self._prev_a >= self._prev_b
            and a < b
        ):
            result = True
        self._prev_a = a
        self._prev_b = b
        return result


# ==============================================================================
# Highest / Lowest
# ==============================================================================
class TaHighest:
    """ta.highest(source, length)"""

    def __init__(self, length: int):
        self._buf: deque[float] = deque(maxlen=length)
        self.length = length

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return None
        self._buf.append(value)
        if len(self._buf) < self.length:
            return None
        return max(self._buf)


class TaLowest:
    """ta.lowest(source, length)"""

    def __init__(self, length: int):
        self._buf: deque[float] = deque(maxlen=length)
        self.length = length

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return None
        self._buf.append(value)
        if len(self._buf) < self.length:
            return None
        return min(self._buf)


# ==============================================================================
# Change
# ==============================================================================
class TaChange:
    """ta.change(source, length=1)"""

    def __init__(self, length: int = 1):
        self.length = length
        self._buf: deque[float] = deque(maxlen=length + 1)

    def update(self, value: float | None) -> float | None:
        if not _is_valid(value):
            return None
        self._buf.append(value)
        if len(self._buf) <= self.length:
            return None
        return value - self._buf[0]
