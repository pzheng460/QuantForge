"""Streaming indicator classes that match vectorized implementations exactly.

Each class processes data bar-by-bar (streaming) and produces values identical
to the vectorized numpy implementations in the strategy core modules.

This enables code reuse between backtest (feeding historical bars) and live
trading (feeding real-time bars), guaranteeing numerical parity.
"""

from collections import deque
from typing import Optional

import numpy as np


class StreamingEMA:
    """Exponential Moving Average (streaming).

    Uses SMA seed for the first `period` values, then recursive EMA update.
    Matches calculate_ema() in core.py:31-41.
    """

    def __init__(self, period: int):
        self._period = int(period)
        self._k = 2.0 / (self._period + 1)
        self._count = 0
        self._value: Optional[float] = None
        self._seed_sum = 0.0

    def update(self, price: float) -> Optional[float]:
        """Process one value. Returns current EMA or None if not ready."""
        self._count += 1

        if self._count < self._period:
            self._seed_sum += price
            return None
        elif self._count == self._period:
            self._seed_sum += price
            self._value = self._seed_sum / self._period
            return self._value
        else:
            self._value = price * self._k + self._value * (1 - self._k)
            return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._count = 0
        self._value = None
        self._seed_sum = 0.0


class StreamingATR:
    """Average True Range with Wilder smoothing (streaming).

    Matches calculate_atr() in core.py:61-85.
    - First TR at index 0 uses high-low (no previous close).
    - SMA seed over tr[1..period] (the first `period` TRs that have a prev close).
    - Then Wilder smoothing: atr = (prev_atr * (period-1) + tr) / period.
    """

    def __init__(self, period: int):
        self._period = int(period)
        self._count = 0  # number of bars processed
        self._prev_close: Optional[float] = None
        self._value: Optional[float] = None
        self._tr_buffer: list[float] = []

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        """Process one bar (high, low, close). Returns current ATR or None."""
        self._count += 1

        # True Range calculation
        if self._prev_close is not None:
            hl = high - low
            hc = abs(high - self._prev_close)
            lc = abs(low - self._prev_close)
            tr = max(hl, hc, lc)
        else:
            tr = high - low

        self._prev_close = close

        # Vectorized core.py uses:
        #   atr[period] = mean(tr[1:period+1])  (skip index 0, use next `period` TRs)
        #   atr[period+1..] = Wilder smoothing
        # So we need `period + 1` bars total before first ATR value.
        # bars 0..period: collect TRs (bar 0 has no prev_close, bars 1..period have)
        # At bar `period` (count == period+1): SMA seed = mean(tr[1..period])

        if self._count <= self._period + 1:
            self._tr_buffer.append(tr)

            if self._count == self._period + 1:
                # SMA seed: mean of tr[1..period] (skip first TR which has no prev close)
                self._value = sum(self._tr_buffer[1:]) / self._period
                return self._value
            return None
        else:
            self._value = (self._value * (self._period - 1) + tr) / self._period
            return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._count = 0
        self._prev_close = None
        self._value = None
        self._tr_buffer = []


class StreamingROC:
    """Rate of Change (streaming).

    ROC = (price - price[n-period]) / price[n-period]
    Matches calculate_roc() in core.py:50-58.
    """

    def __init__(self, period: int):
        self._period = int(period)
        self._history: deque[float] = deque(maxlen=self._period + 1)
        self._value: Optional[float] = None

    def update(self, price: float) -> Optional[float]:
        """Process one value. Returns current ROC or None if not ready."""
        self._history.append(price)

        if len(self._history) <= self._period:
            self._value = None
            return None

        prev = self._history[0]
        if prev != 0:
            self._value = (price - prev) / prev
        else:
            self._value = None
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._history.clear()
        self._value = None


class StreamingSMA:
    """Simple Moving Average (streaming).

    Matches calculate_sma() in core.py:88-94.
    """

    def __init__(self, period: int):
        self._period = int(period)
        self._window: deque[float] = deque(maxlen=self._period)
        self._value: Optional[float] = None

    def update(self, value: float) -> Optional[float]:
        """Process one value. Returns current SMA or None if not ready."""
        self._window.append(value)

        if len(self._window) < self._period:
            self._value = None
            return None

        self._value = sum(self._window) / self._period
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._window.clear()
        self._value = None


class StreamingADX:
    """Average Directional Index with Wilder smoothing (streaming).

    Matches calculate_adx() in core.py:97-163.

    Process:
    1. Compute TR, +DM, -DM per bar (need prev high/low/close)
    2. Wilder-smooth TR, +DM, -DM (first `period` values: sum seed)
    3. Compute +DI, -DI, DX from smoothed values
    4. ADX = Wilder-smoothed DX (seed from first `period` DX values)
    """

    def __init__(self, period: int = 14):
        self._period = int(period)
        self._count = 0  # bars processed (0-indexed internally)
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None
        self._prev_close: Optional[float] = None

        # Wilder smoothed accumulators
        self._atr_s = 0.0
        self._plus_dm_s = 0.0
        self._minus_dm_s = 0.0

        # DX history for ADX seed
        self._dx_values: list[float] = []

        self._value: Optional[float] = None

        # Track smoothing phase
        self._dm_init_count = (
            0  # how many (TR, +DM, -DM) tuples collected (from bar 1 onward)
        )

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        """Process one bar. Returns current ADX or None if not ready."""
        self._count += 1

        if self._prev_high is not None:
            # TR, +DM, -DM (same as core.py:118-128)
            hl = high - low
            hc = abs(high - self._prev_close)
            lc = abs(low - self._prev_close)
            tr = max(hl, hc, lc)

            up = high - self._prev_high
            down = self._prev_low - low
            plus_dm = up if (up > down and up > 0) else 0.0
            minus_dm = down if (down > up and down > 0) else 0.0

            self._dm_init_count += 1

            if self._dm_init_count <= self._period:
                # Accumulate for initial sum (core.py:135-137)
                self._atr_s += tr
                self._plus_dm_s += plus_dm
                self._minus_dm_s += minus_dm

                if self._dm_init_count == self._period:
                    # First smoothed values ready, compute first DX (core.py:146-151)
                    if self._atr_s > 0:
                        plus_di = 100.0 * self._plus_dm_s / self._atr_s
                        minus_di = 100.0 * self._minus_dm_s / self._atr_s
                        di_sum = plus_di + minus_di
                        dx = (
                            100.0 * abs(plus_di - minus_di) / di_sum
                            if di_sum > 0
                            else 0.0
                        )
                        self._dx_values.append(dx)
            else:
                # Wilder smoothing (core.py:139-142)
                self._atr_s = self._atr_s - self._atr_s / self._period + tr
                self._plus_dm_s = (
                    self._plus_dm_s - self._plus_dm_s / self._period + plus_dm
                )
                self._minus_dm_s = (
                    self._minus_dm_s - self._minus_dm_s / self._period + minus_dm
                )

                if self._atr_s > 0:
                    plus_di = 100.0 * self._plus_dm_s / self._atr_s
                    minus_di = 100.0 * self._minus_dm_s / self._atr_s
                    di_sum = plus_di + minus_di
                    dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
                    self._dx_values.append(dx)

                    # ADX seed or smoothing (core.py:154-161)
                    # Vectorized code places first ADX at index 2*period,
                    # using DX[period..2*period-1] (first `period` DX values).
                    # DX at index 2*period (period+1'th DX) is skipped.
                    # We match by requiring period+1 DX values before seeding.
                    if self._value is None and len(self._dx_values) >= self._period + 1:
                        self._value = (
                            sum(self._dx_values[: self._period]) / self._period
                        )
                    elif self._value is not None:
                        self._value = (
                            self._value * (self._period - 1) + dx
                        ) / self._period

        self._prev_high = high
        self._prev_low = low
        self._prev_close = close

        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._count = 0
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._atr_s = 0.0
        self._plus_dm_s = 0.0
        self._minus_dm_s = 0.0
        self._dx_values = []
        self._value = None
        self._dm_init_count = 0


class StreamingBB:
    """Bollinger Bands (streaming).

    Maintains a rolling window and computes SMA, upper and lower bands
    using population std (ddof=0).
    Matches calculate_bollinger_bands() in bollinger_band/core.py.
    """

    def __init__(self, period: int = 20, multiplier: float = 2.0):
        self._period = int(period)
        self._multiplier = multiplier
        self._window: deque[float] = deque(maxlen=self._period)
        self._sma: Optional[float] = None
        self._upper: Optional[float] = None
        self._lower: Optional[float] = None

    def update(
        self, price: float
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Process one value. Returns (sma, upper, lower) or (None, None, None)."""
        self._window.append(price)

        if len(self._window) < self._period:
            return None, None, None

        arr = np.array(self._window)
        m = float(np.mean(arr))
        s = float(np.std(arr, ddof=0))
        self._sma = m
        self._upper = m + self._multiplier * s
        self._lower = m - self._multiplier * s
        return self._sma, self._upper, self._lower

    @property
    def sma(self) -> Optional[float]:
        return self._sma

    @property
    def upper(self) -> Optional[float]:
        return self._upper

    @property
    def lower(self) -> Optional[float]:
        return self._lower

    def reset(self):
        self._window.clear()
        self._sma = None
        self._upper = None
        self._lower = None


class StreamingRSI:
    """Relative Strength Index with Wilder's smoothing (streaming).

    Matches calculate_rsi() in vwap/core.py.
    - SMA seed for first `period` deltas.
    - Then Wilder smoothing: avg = (prev * (period-1) + current) / period.
    """

    def __init__(self, period: int = 14):
        self._period = int(period)
        self._count = 0
        self._prev_price: Optional[float] = None
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self._value: Optional[float] = None
        self._gains: list[float] = []
        self._losses: list[float] = []

    def update(self, price: float) -> Optional[float]:
        """Process one price. Returns RSI or None if not ready."""
        if self._prev_price is None:
            self._prev_price = price
            self._count += 1
            return None

        self._count += 1
        delta = price - self._prev_price
        self._prev_price = price

        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0

        if self._avg_gain is None:
            # Accumulating for SMA seed
            self._gains.append(gain)
            self._losses.append(loss)

            if len(self._gains) >= self._period:
                self._avg_gain = sum(self._gains) / self._period
                self._avg_loss = sum(self._losses) / self._period

                if self._avg_loss < 1e-10:
                    self._value = 100.0
                else:
                    rs = self._avg_gain / self._avg_loss
                    self._value = 100.0 - 100.0 / (1.0 + rs)
                return self._value
            return None
        else:
            self._avg_gain = (self._avg_gain * (self._period - 1) + gain) / self._period
            self._avg_loss = (self._avg_loss * (self._period - 1) + loss) / self._period

            if self._avg_loss < 1e-10:
                self._value = 100.0
            else:
                rs = self._avg_gain / self._avg_loss
                self._value = 100.0 - 100.0 / (1.0 + rs)
            return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self):
        self._count = 0
        self._prev_price = None
        self._avg_gain = None
        self._avg_loss = None
        self._value = None
        self._gains = []
        self._losses = []
