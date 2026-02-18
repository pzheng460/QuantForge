"""
NexusTrader Indicator wrapper for VWAP Mean Reversion strategy.

This module wraps the core algorithms into a NexusTrader Indicator class
that can be registered with a Strategy.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.vwap.core import VWAPConfig


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"  # Z <= -entry AND RSI < oversold
    SELL = "sell"  # Z >= entry AND RSI > overbought
    CLOSE = "close"  # |Z| < exit threshold (price returned to VWAP)


class VWAPIndicator(Indicator):
    """
    Indicator that calculates VWAP, Z-Score, and RSI for mean-reversion signals.

    Provides:
    - Daily-resetting VWAP
    - Rolling Z-Score of price deviation from VWAP
    - RSI (Wilder's smoothing)
    - Trading signals based on Z-Score + RSI confirmation
    """

    def __init__(
        self,
        config: Optional[VWAPConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_5,
    ):
        self._config = config or VWAPConfig()
        if warmup_period is None:
            warmup_period = self._config.std_window + self._config.rsi_period

        super().__init__(
            params={
                "std_window": self._config.std_window,
                "rsi_period": self._config.rsi_period,
                "zscore_entry": self._config.zscore_entry,
            },
            name="VWAP",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Price/volume history for Z-score rolling window
        max_history = self._config.std_window + 50
        self._close_history: deque[float] = deque(maxlen=max_history)
        self._vwap_history: deque[float] = deque(maxlen=max_history)

        # RSI state (Wilder's smoothing — incremental)
        self._rsi_prices: deque[float] = deque(maxlen=self._config.rsi_period + 2)
        self._rsi_avg_gain: Optional[float] = None
        self._rsi_avg_loss: Optional[float] = None
        self._rsi_count: int = 0

        # VWAP cumulative state (resets daily)
        self._cum_tp_vol: float = 0.0
        self._cum_vol: float = 0.0
        self._current_day: Optional[int] = None  # day-of-year for reset detection

        # Current indicator values
        self._vwap: Optional[float] = None
        self._zscore: float = 0.0
        self._rsi: float = 50.0
        self._std: float = 0.0
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update indicator values."""
        if not self._is_warmed_up:
            self._warmup_data_count += 1
            if self._warmup_data_count >= self.warmup_period:
                self._is_warmed_up = True

        price = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        volume = float(kline.volume)
        self._last_price = price

        # Detect day boundary for VWAP reset
        ts = kline.timestamp
        if hasattr(ts, "date"):
            day = ts.timetuple().tm_yday
        else:
            # Millisecond timestamp
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            day = dt.timetuple().tm_yday

        if self._current_day is not None and day != self._current_day:
            self._cum_tp_vol = 0.0
            self._cum_vol = 0.0
        self._current_day = day

        # Update VWAP
        typical_price = (high + low + price) / 3.0
        if volume > 0:
            self._cum_tp_vol += typical_price * volume
            self._cum_vol += volume
        if self._cum_vol > 0:
            self._vwap = self._cum_tp_vol / self._cum_vol

        # Store history for Z-score
        self._close_history.append(price)
        if self._vwap is not None:
            self._vwap_history.append(self._vwap)

        # Calculate Z-Score (rolling std of deviation)
        if (
            len(self._close_history) >= self._config.std_window
            and len(self._vwap_history) >= self._config.std_window
        ):
            recent_closes = np.array(
                list(self._close_history)[-self._config.std_window :]
            )
            recent_vwap = np.array(list(self._vwap_history)[-self._config.std_window :])
            deviations = recent_closes - recent_vwap
            self._std = float(np.std(deviations, ddof=0))

            if self._std > 1e-10 and self._vwap is not None:
                self._zscore = (price - self._vwap) / self._std
            else:
                self._zscore = 0.0

        # Update RSI (incremental Wilder's)
        self._update_rsi(price)

        # Generate signal
        self._update_signal()

    def _update_rsi(self, price: float) -> None:
        """Incrementally update RSI using Wilder's smoothing."""
        self._rsi_prices.append(price)
        self._rsi_count += 1
        period = self._config.rsi_period

        if self._rsi_count < period + 1:
            return

        if self._rsi_avg_gain is None:
            # Initialize with SMA seed
            prices = list(self._rsi_prices)
            deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains = [d if d > 0 else 0.0 for d in deltas[-period:]]
            losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
            self._rsi_avg_gain = sum(gains) / period
            self._rsi_avg_loss = sum(losses) / period
        else:
            prices = list(self._rsi_prices)
            delta = prices[-1] - prices[-2]
            gain = delta if delta > 0 else 0.0
            loss = -delta if delta < 0 else 0.0
            self._rsi_avg_gain = (self._rsi_avg_gain * (period - 1) + gain) / period
            self._rsi_avg_loss = (self._rsi_avg_loss * (period - 1) + loss) / period

        if self._rsi_avg_loss < 1e-10:
            self._rsi = 100.0
        else:
            rs = self._rsi_avg_gain / self._rsi_avg_loss
            self._rsi = 100.0 - 100.0 / (1.0 + rs)

    def _update_signal(self) -> None:
        """Generate trading signal based on Z-Score and RSI."""
        if self._vwap is None or self._last_price is None:
            self._signal = Signal.HOLD
            return

        z = self._zscore
        r = self._rsi

        # Entry conditions
        if z <= -self._config.zscore_entry and r < self._config.rsi_oversold:
            self._signal = Signal.BUY
        elif z >= self._config.zscore_entry and r > self._config.rsi_overbought:
            self._signal = Signal.SELL
        elif abs(z) <= abs(self._config.zscore_exit):
            self._signal = Signal.CLOSE
        else:
            self._signal = Signal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    @property
    def value(self) -> dict:
        return {
            "vwap": self._vwap,
            "zscore": self._zscore,
            "rsi": self._rsi,
            "std": self._std,
            "signal": self._signal.value,
        }

    @property
    def vwap(self) -> Optional[float]:
        return self._vwap

    @property
    def zscore(self) -> float:
        return self._zscore

    @property
    def rsi(self) -> float:
        return self._rsi

    @property
    def std(self) -> float:
        return self._std

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Check if stop loss should be triggered.

        Triggers on:
        - |Z| >= zscore_stop (model failure)
        - Price move > stop_loss_pct against position
        """
        if entry_price <= 0:
            return False

        # Price-based stop
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        if pnl_pct < -self._config.stop_loss_pct:
            return True

        # Z-Score model failure
        if abs(self._zscore) > self._config.zscore_stop:
            return True

        return False

    def reset(self) -> None:
        self._close_history.clear()
        self._vwap_history.clear()
        self._rsi_prices.clear()
        self._rsi_avg_gain = None
        self._rsi_avg_loss = None
        self._rsi_count = 0
        self._cum_tp_vol = 0.0
        self._cum_vol = 0.0
        self._current_day = None
        self._vwap = None
        self._zscore = 0.0
        self._rsi = 50.0
        self._std = 0.0
        self._signal = Signal.HOLD
        self._last_price = None
        self.reset_warmup()
