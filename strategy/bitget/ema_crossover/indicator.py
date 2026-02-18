"""
NexusTrader Indicator wrapper for EMA Crossover strategy.

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

from strategy.bitget.ema_crossover.core import (
    EMAConfig,
    calculate_ema_single,
)


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"  # Fast EMA crosses above slow EMA
    SELL = "sell"  # Fast EMA crosses below slow EMA
    CLOSE = "close"


class EMACrossoverIndicator(Indicator):
    """
    Indicator that calculates fast/slow EMA crossover signals.

    This indicator provides:
    - Fast EMA value
    - Slow EMA value
    - Crossover detection (golden cross / death cross)
    - Trading signals based on crossover events
    """

    def __init__(
        self,
        config: Optional[EMAConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ):
        self._config = config or EMAConfig()
        if warmup_period is None:
            # Need at least slow_period bars for initial SMA seed + a few extra
            warmup_period = self._config.slow_period + 10

        super().__init__(
            params={
                "fast_period": self._config.fast_period,
                "slow_period": self._config.slow_period,
            },
            name="EMACrossover",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Price history for initial SMA seeding
        self._price_history: deque[float] = deque(maxlen=self._config.slow_period + 10)

        # EMA state
        self._fast_ema: Optional[float] = None
        self._slow_ema: Optional[float] = None
        self._prev_fast_ema: Optional[float] = None
        self._prev_slow_ema: Optional[float] = None
        self._fast_seeded: bool = False
        self._slow_seeded: bool = False

        # Signal state
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None
        self._bar_count: int = 0

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update indicator values."""
        # Manual warmup tracking
        if not self._is_warmed_up:
            self._warmup_data_count += 1
            if self._warmup_data_count >= self.warmup_period:
                self._is_warmed_up = True

        price = float(kline.close)
        self._last_price = price
        self._bar_count += 1

        self._price_history.append(price)

        # Save previous EMA values for crossover detection
        self._prev_fast_ema = self._fast_ema
        self._prev_slow_ema = self._slow_ema

        # Seed fast EMA with SMA once we have enough data
        if not self._fast_seeded:
            if len(self._price_history) >= self._config.fast_period:
                prices = list(self._price_history)
                self._fast_ema = float(np.mean(prices[-self._config.fast_period :]))
                self._fast_seeded = True
        else:
            self._fast_ema = calculate_ema_single(
                self._fast_ema, price, self._config.fast_period
            )

        # Seed slow EMA with SMA once we have enough data
        if not self._slow_seeded:
            if len(self._price_history) >= self._config.slow_period:
                prices = list(self._price_history)
                self._slow_ema = float(np.mean(prices[-self._config.slow_period :]))
                self._slow_seeded = True
        else:
            self._slow_ema = calculate_ema_single(
                self._slow_ema, price, self._config.slow_period
            )

        # Generate signal once both EMAs are ready
        self._update_signal()

    def _update_signal(self) -> None:
        """Generate trading signal based on EMA crossover."""
        if (
            self._fast_ema is None
            or self._slow_ema is None
            or self._prev_fast_ema is None
            or self._prev_slow_ema is None
        ):
            self._signal = Signal.HOLD
            return

        prev_diff = self._prev_fast_ema - self._prev_slow_ema
        curr_diff = self._fast_ema - self._slow_ema

        # Golden cross: fast crosses above slow
        if prev_diff <= 0 and curr_diff > 0:
            self._signal = Signal.BUY
        # Death cross: fast crosses below slow
        elif prev_diff >= 0 and curr_diff < 0:
            self._signal = Signal.SELL
        else:
            self._signal = Signal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # Properties

    @property
    def value(self) -> dict:
        """Get all indicator values as a dictionary."""
        return {
            "fast_ema": self._fast_ema,
            "slow_ema": self._slow_ema,
            "signal": self._signal.value,
        }

    @property
    def fast_ema(self) -> Optional[float]:
        return self._fast_ema

    @property
    def slow_ema(self) -> Optional[float]:
        return self._slow_ema

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
        """Reset indicator to initial state."""
        self._price_history.clear()
        self._fast_ema = None
        self._slow_ema = None
        self._prev_fast_ema = None
        self._prev_slow_ema = None
        self._fast_seeded = False
        self._slow_seeded = False
        self._signal = Signal.HOLD
        self._last_price = None
        self._bar_count = 0
        self.reset_warmup()
