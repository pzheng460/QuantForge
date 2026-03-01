"""Shared Funding Rate signal core for backtest and live trading.

This module contains FundingRateSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.funding_rate.core import FundingRateConfig


# Signal constants
HOLD = 0
SELL = -1
CLOSE = 2


class FundingRateSignalCore:
    """Shared signal logic for Funding Rate Arbitrage.

    Only opens SHORT positions to collect positive funding rates.
    - Enter short before settlement when funding rate is positive
    - Exit after settlement
    - SMA trend filter to avoid shorting into strong uptrends
    - Hard stop loss and adverse move check
    """

    def __init__(
        self,
        config: FundingRateConfig,
        min_holding_bars: int = 1,
        cooldown_bars: int = 1,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # SMA indicator
        self._sma = StreamingSMA(config.price_sma_period)

        # Funding rate tracking
        self._funding_rates: deque[float] = deque(maxlen=config.funding_lookback)
        self._avg_funding_rate: float = 0.0

        # Position management state
        self.position = 0  # 0=flat, -1=short (never long)
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0

    def set_funding_rate(self, rate: float) -> None:
        """Update current funding rate from external source."""
        self._funding_rates.append(rate)
        if len(self._funding_rates) > 0:
            self._avg_funding_rate = float(np.mean(list(self._funding_rates)))

    def update_indicators_only(self, close: float) -> None:
        """Update SMA indicator without generating a signal."""
        self._sma.update(close)
        self.bar_index += 1

    def update(
        self,
        close: float,
        hours_to_next: float,
        hours_since_last: float,
    ) -> int:
        """Process one bar and return a trading signal.

        Args:
            close: Close price.
            hours_to_next: Hours until next funding settlement.
            hours_since_last: Hours since last funding settlement.

        Returns:
            Signal value: HOLD(0), SELL(-1), or CLOSE(2).
        """
        sma = self._sma.update(close)
        self.bar_index += 1
        i = self.bar_index

        price = close

        # Need SMA to be valid
        if sma is None:
            return HOLD

        # ---- 1. Stop-loss check ----
        if self.position == -1 and self.entry_price > 0:
            adverse_pct = (price - self.entry_price) / self.entry_price
            if adverse_pct > self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- 2. Exit logic: close after funding settlement ----
        if self.position == -1:
            bars_held = i - self.entry_bar
            if (
                hours_since_last <= self._config.hours_after_funding
                and hours_since_last > 0
                and bars_held >= self._min_holding_bars
            ):
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

            # Adverse move check
            if self.entry_price > 0:
                adverse_pct = (price - self.entry_price) / self.entry_price
                if adverse_pct > self._config.max_adverse_move_pct:
                    self.position = 0
                    self.entry_price = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE

        # ---- 3. Entry logic ----
        if i < self.cooldown_until:
            return HOLD

        if self.position == 0:
            in_entry_window = hours_to_next <= self._config.hours_before_funding
            fr = self._avg_funding_rate
            funding_ok = (
                self._config.min_funding_rate <= fr <= self._config.max_funding_rate
            )
            trend_ok = price <= sma * (1.0 + self._config.max_adverse_move_pct)

            if in_entry_window and funding_ok and trend_ok:
                self.position = -1
                self.entry_bar = i
                self.entry_price = price
                return SELL

        return HOLD

    def reset(self):
        """Reset all state."""
        self._sma.reset()
        self._funding_rates.clear()
        self._avg_funding_rate = 0.0
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0

    # ---- Indicator value properties ----

    @property
    def sma_value(self) -> Optional[float]:
        return self._sma.value

    @property
    def avg_funding_rate(self) -> float:
        return self._avg_funding_rate

    def get_signal(self, hours_to_next: float, hours_since_last: float) -> int:
        """Compute raw signal from current indicator values (no position management)."""
        sma = self._sma.value
        if sma is None or sma <= 0:
            return HOLD

        if 0 < hours_since_last <= self._config.hours_after_funding:
            return CLOSE

        in_entry_window = hours_to_next <= self._config.hours_before_funding
        if not in_entry_window:
            return HOLD

        fr = self._avg_funding_rate
        if fr < self._config.min_funding_rate or fr > self._config.max_funding_rate:
            return HOLD

        price = list(self._sma._window)[-1] if self._sma._window else 0.0
        if price > sma * (1.0 + self._config.max_adverse_move_pct):
            return HOLD

        return SELL
