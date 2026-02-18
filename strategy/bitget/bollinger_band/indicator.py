"""
NexusTrader Indicator wrapper for Bollinger Band Mean Reversion strategy.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.bollinger_band.core import (
    BBConfig,
    calculate_bb_single,
)


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"  # Price below lower band (oversold)
    SELL = "sell"  # Price above upper band (overbought)
    CLOSE = "close"  # Price returned to SMA


class BollingerBandIndicator(Indicator):
    """
    Indicator that calculates Bollinger Bands and generates mean-reversion signals.

    Provides:
    - SMA, upper band, lower band
    - %B indicator (where price is relative to bands)
    - Trading signals based on band touches and mean reversion
    """

    def __init__(
        self,
        config: Optional[BBConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ):
        self._config = config or BBConfig()
        # Trend SMA needs more history than BB alone
        trend_len = self._config.bb_period * self._config.trend_sma_multiplier
        max_period = max(self._config.bb_period, trend_len)
        if warmup_period is None:
            warmup_period = max_period + 10

        super().__init__(
            params={
                "bb_period": self._config.bb_period,
                "bb_multiplier": self._config.bb_multiplier,
                "trend_bias": self._config.trend_bias or "none",
            },
            name="BollingerBand",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        self._price_history: deque[float] = deque(maxlen=max_period + 10)

        # Band values
        self._sma: Optional[float] = None
        self._upper: Optional[float] = None
        self._lower: Optional[float] = None
        self._pct_b: Optional[float] = None  # %B indicator

        # Trend detection
        self._trend_sma: Optional[float] = None
        self._trend_sma_len: int = trend_len

        # Signal state
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update indicator values."""
        if not self._is_warmed_up:
            self._warmup_data_count += 1
            if self._warmup_data_count >= self.warmup_period:
                self._is_warmed_up = True

        price = float(kline.close)
        self._last_price = price
        self._price_history.append(price)

        # Calculate bands once we have enough data
        if len(self._price_history) >= self._config.bb_period:
            window = np.array(list(self._price_history)[-self._config.bb_period :])
            self._sma, self._upper, self._lower = calculate_bb_single(
                window, self._config.bb_multiplier
            )

            # %B = (price - lower) / (upper - lower)
            band_width = self._upper - self._lower
            if band_width > 1e-10:
                self._pct_b = (price - self._lower) / band_width
            else:
                self._pct_b = 0.5

        # Trend SMA for auto bias detection
        if len(self._price_history) >= self._trend_sma_len:
            trend_window = list(self._price_history)[-self._trend_sma_len :]
            self._trend_sma = float(np.mean(trend_window))

        self._update_signal()

    def _resolve_bias(self) -> str | None:
        """Return the effective bias: 'long_only', 'short_only', or None."""
        bias = self._config.trend_bias
        if bias == "auto":
            if self._trend_sma is None or self._last_price is None:
                return None
            return "short_only" if self._last_price < self._trend_sma else "long_only"
        return bias

    def _update_signal(self) -> None:
        """Generate trading signal based on Bollinger Band position."""
        if self._sma is None or self._upper is None or self._lower is None:
            self._signal = Signal.HOLD
            return

        price = self._last_price

        # Price below lower band -> oversold -> BUY
        if price <= self._lower:
            self._signal = Signal.BUY
        # Price above upper band -> overbought -> SELL
        elif price >= self._upper:
            self._signal = Signal.SELL
        # Price returned near SMA -> close position
        elif self._sma is not None:
            band_half = self._upper - self._sma
            if band_half > 1e-10:
                distance_ratio = abs(price - self._sma) / band_half
                if distance_ratio < self._config.exit_threshold:
                    self._signal = Signal.CLOSE
                else:
                    self._signal = Signal.HOLD
            else:
                self._signal = Signal.HOLD
        else:
            self._signal = Signal.HOLD

        # Apply trend bias filter (only suppress entry signals, never suppress CLOSE)
        bias = self._resolve_bias()
        if bias == "short_only" and self._signal == Signal.BUY:
            self._signal = Signal.HOLD
        elif bias == "long_only" and self._signal == Signal.SELL:
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
            "sma": self._sma,
            "upper": self._upper,
            "lower": self._lower,
            "pct_b": self._pct_b,
            "trend_sma": self._trend_sma,
            "trend_bias": self._resolve_bias(),
            "signal": self._signal.value,
        }

    @property
    def sma(self) -> Optional[float]:
        return self._sma

    @property
    def trend_sma(self) -> Optional[float]:
        return self._trend_sma

    @property
    def upper_band(self) -> Optional[float]:
        return self._upper

    @property
    def lower_band(self) -> Optional[float]:
        return self._lower

    @property
    def pct_b(self) -> Optional[float]:
        return self._pct_b

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Check if stop loss should be triggered (price-based only)."""
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct < -self._config.stop_loss_pct

    def reset(self) -> None:
        self._price_history.clear()
        self._sma = None
        self._upper = None
        self._lower = None
        self._pct_b = None
        self._trend_sma = None
        self._signal = Signal.HOLD
        self._last_price = None
        self.reset_warmup()
