"""
NexusTrader Indicator wrapper for Funding Rate Arbitrage strategy.

This module wraps the core algorithms into a NexusTrader Indicator class
that can be registered with a Strategy for live/demo trading.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.strategies.funding_rate.core import FundingRateConfig, calculate_sma


class Signal(Enum):
    """Trading signals for funding rate arbitrage."""

    HOLD = "hold"
    SELL = "sell"    # Open short to collect positive funding rate
    CLOSE = "close"  # Close short after funding settlement


class FundingRateIndicator(Indicator):
    """
    Indicator that tracks SMA and provides entry/exit signals
    for funding rate arbitrage.

    Maintains a rolling window of close prices for SMA calculation.
    The actual funding rate data comes from the strategy via set_funding_rate().
    """

    def __init__(
        self,
        config: FundingRateConfig,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        super().__init__()
        self.config = config
        self.kline_interval = kline_interval

        # Rolling price window for SMA
        self._prices: deque = deque(maxlen=max(config.price_sma_period + 10, 100))

        # Current state
        self.sma: float = 0.0
        self.current_price: float = 0.0
        self.current_funding_rate: float = 0.0
        self.avg_funding_rate: float = 0.0

        # Warmup tracking
        self._bar_count: int = 0
        self._warmup_target: int = config.price_sma_period

        # Funding rate history
        self._funding_rates: deque = deque(maxlen=config.funding_lookback)

    @property
    def is_warmed_up(self) -> bool:
        """Whether the indicator has enough data."""
        return self._bar_count >= self._warmup_target

    def set_funding_rate(self, rate: float) -> None:
        """Update current funding rate from external source.

        Args:
            rate: Current funding rate value (e.g. 0.0001 = 0.01%)
        """
        self.current_funding_rate = rate
        self._funding_rates.append(rate)
        if len(self._funding_rates) > 0:
            self.avg_funding_rate = np.mean(list(self._funding_rates))

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update indicators.

        Args:
            kline: New kline data.
        """
        price = float(kline.close)
        self.current_price = price
        self._prices.append(price)
        self._bar_count += 1

        # Update SMA
        if len(self._prices) >= self.config.price_sma_period:
            prices_arr = np.array(list(self._prices))
            self.sma = np.mean(prices_arr[-self.config.price_sma_period:])

    def get_signal(self, hours_to_next_settlement: float, hours_since_last_settlement: float) -> Signal:
        """Generate trading signal based on current state.

        Args:
            hours_to_next_settlement: Hours until next funding settlement.
            hours_since_last_settlement: Hours since last funding settlement.

        Returns:
            Trading signal.
        """
        if not self.is_warmed_up:
            return Signal.HOLD

        price = self.current_price
        sma = self.sma

        if sma <= 0 or price <= 0:
            return Signal.HOLD

        # Check if it's time to close (after settlement)
        if 0 < hours_since_last_settlement <= self.config.hours_after_funding:
            return Signal.CLOSE

        # Check if it's time to enter (before settlement)
        in_entry_window = hours_to_next_settlement <= self.config.hours_before_funding

        if not in_entry_window:
            return Signal.HOLD

        # Check funding rate conditions
        fr = self.avg_funding_rate
        if fr < self.config.min_funding_rate or fr > self.config.max_funding_rate:
            return Signal.HOLD

        # Trend filter: don't short into strong uptrend
        if price > sma * (1.0 + self.config.max_adverse_move_pct):
            return Signal.HOLD

        return Signal.SELL

    def should_stop_loss(self, entry_price: float, current_price: float) -> bool:
        """Check if stop loss should be triggered for a short position.

        Args:
            entry_price: Position entry price.
            current_price: Current market price.

        Returns:
            True if stop loss should trigger.
        """
        if entry_price <= 0:
            return False
        # Short position: adverse = price going UP
        adverse_pct = (current_price - entry_price) / entry_price
        return adverse_pct > self.config.stop_loss_pct

    # ---- Required Indicator interface methods ----

    def on_kline(self, kline: Kline) -> None:
        self.handle_kline(kline)

    def on_trade(self, trade: Trade) -> None:
        pass

    def on_bookl1(self, bookl1: BookL1) -> None:
        pass

    def on_bookl2(self, bookl2: BookL2) -> None:
        pass
