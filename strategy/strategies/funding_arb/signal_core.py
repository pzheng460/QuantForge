"""Shared Funding Arb signal core for backtest and live trading.

This module contains FundingArbSignalCore — a streaming, bar-by-bar signal
generator for delta-neutral funding rate arbitrage. Both the backtest signal
generator and the live indicator wrapper delegate to this class, guaranteeing
100% code parity.

Simpler than FundingRateSignalCore: no SMA trend filter, no adverse move
check. Pure funding rate threshold + settlement timing.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from strategy.strategies.funding_arb.core import FundingArbConfig


# Signal constants
HOLD = 0
SELL = -1
CLOSE = 2


class FundingArbSignalCore:
    """Shared signal logic for delta-neutral Funding Rate Arbitrage.

    Only opens SHORT perp positions to collect positive funding rates.
    Assumes a conceptual spot long hedge (not modeled in backtest).
    - Enter short before settlement when smoothed funding rate > threshold
    - Exit after settlement
    - Hard stop loss
    """

    def __init__(
        self,
        config: FundingArbConfig,
        min_holding_bars: int = 1,
        cooldown_bars: int = 1,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

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
        """Update state without generating a signal (warmup mode)."""
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
        self.bar_index += 1
        i = self.bar_index
        price = close

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

        # ---- 3. Entry logic ----
        if i < self.cooldown_until:
            return HOLD

        if self.position == 0:
            in_entry_window = hours_to_next <= self._config.hours_before_funding
            fr = self._avg_funding_rate
            funding_ok = (
                self._config.min_funding_rate <= fr <= self._config.max_funding_rate
            )

            if in_entry_window and funding_ok:
                self.position = -1
                self.entry_bar = i
                self.entry_price = price
                return SELL

        return HOLD

    def reset(self):
        """Reset all state."""
        self._funding_rates.clear()
        self._avg_funding_rate = 0.0
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0

    # ---- Indicator value properties ----

    @property
    def avg_funding_rate(self) -> float:
        return self._avg_funding_rate

    def get_signal(self, hours_to_next: float, hours_since_last: float) -> int:
        """Compute raw signal from current state (no position management).

        Used by the live indicator wrapper in warmup mode.
        """
        if 0 < hours_since_last <= self._config.hours_after_funding:
            return CLOSE

        in_entry_window = hours_to_next <= self._config.hours_before_funding
        if not in_entry_window:
            return HOLD

        fr = self._avg_funding_rate
        if fr < self._config.min_funding_rate or fr > self._config.max_funding_rate:
            return HOLD

        return SELL
