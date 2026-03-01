"""Shared Grid Trading signal core for backtest and live trading.

This module contains GridSignalCore — a streaming, bar-by-bar signal
generator that tracks grid levels and generates entry/exit signals
based on grid line crossings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingATR, StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.grid_trading.core import GridConfig


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class GridSignalCore:
    """Shared signal logic for Grid Trading backtest and live trading.

    Uses SMA + ATR for dynamic grid bounds:
    - BUY when price drops N grid lines from peak (mean reversion buy)
    - CLOSE long when price rises M grid lines from trough
    - SELL when price rises N grid lines from trough
    - CLOSE short when price drops M grid lines from peak
    """

    def __init__(
        self,
        config: GridConfig,
        min_holding_bars: int = 1,
        cooldown_bars: int = 0,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # Streaming indicators
        self._sma = StreamingSMA(config.sma_period)
        self._atr = StreamingATR(config.atr_period)

        # Grid state
        self._grid_lines: Optional[np.ndarray] = None
        self._current_level: int = 0
        self._peak_level: int = 0
        self._trough_level: int = 999

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0
        self._last_recalc: int = 0

    def update_indicators_only(self, close: float, high: float, low: float) -> None:
        """Update all indicators without generating a trading signal."""
        self._sma.update(close)
        self._atr.update(high, low, close)
        self.bar_index += 1

    def update(self, close: float, high: float, low: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        sma_val = self._sma.update(close)
        atr_val = self._atr.update(high, low, close)

        self.bar_index += 1
        i = self.bar_index

        price = close

        # Need SMA and ATR to be ready
        if sma_val is None or atr_val is None or atr_val <= 0:
            return HOLD

        # Recalculate grid periodically
        if self._grid_lines is None or (
            i - self._last_recalc >= self._config.recalc_period
        ):
            center = sma_val
            half_range = self._config.atr_multiplier * atr_val
            upper = center + half_range
            lower = center - half_range
            if upper <= lower:
                return HOLD
            self._grid_lines = np.linspace(lower, upper, self._config.grid_count + 1)
            self._last_recalc = i
            # Reset tracking on grid recalc
            self._current_level = int(np.searchsorted(self._grid_lines, price))
            self._current_level = max(
                0, min(self._config.grid_count, self._current_level)
            )
            self._peak_level = self._current_level
            self._trough_level = self._current_level

        if self._grid_lines is None:
            return HOLD

        # Get current grid level
        new_level = int(np.searchsorted(self._grid_lines, price))
        new_level = max(0, min(self._config.grid_count, new_level))

        # Stop loss
        if self.position != 0 and self.entry_price > 0:
            if self.position == 1:
                loss = (self.entry_price - price) / self.entry_price
            else:
                loss = (price - self.entry_price) / self.entry_price
            if loss > self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                self.cooldown_until = i + self._cooldown_bars
                self._current_level = new_level
                return CLOSE

        if i < self.cooldown_until:
            self._current_level = new_level
            return HOLD

        # Track peak/trough
        if new_level > self._peak_level:
            self._peak_level = new_level
        if new_level < self._trough_level:
            self._trough_level = new_level

        entry_lines = self._config.entry_lines
        profit_lines = self._config.profit_lines

        if self.position == 0:
            # FLAT: look for entry
            if (
                self._peak_level - new_level >= entry_lines
                and new_level <= self._config.grid_count // 2
            ):
                self.position = 1
                self.entry_price = price
                self._trough_level = new_level
                self._peak_level = new_level
                self._current_level = new_level
                return BUY

            if (
                new_level - self._trough_level >= entry_lines
                and new_level >= self._config.grid_count // 2
            ):
                self.position = -1
                self.entry_price = price
                self._peak_level = new_level
                self._trough_level = new_level
                self._current_level = new_level
                return SELL

        elif self.position == 1:
            # LONG: look for take-profit
            if new_level - self._trough_level >= profit_lines:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                self.cooldown_until = i + self._cooldown_bars
                self._current_level = new_level
                return CLOSE

        elif self.position == -1:
            # SHORT: look for take-profit
            if self._peak_level - new_level >= profit_lines:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                self.cooldown_until = i + self._cooldown_bars
                self._current_level = new_level
                return CLOSE

        self._current_level = new_level
        return HOLD

    def reset(self):
        """Reset all state."""
        self._sma.reset()
        self._atr.reset()
        self._grid_lines = None
        self._current_level = 0
        self._peak_level = 0
        self._trough_level = 999
        self.position = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0
        self._last_recalc = 0

    # ---- Indicator value properties ----

    @property
    def sma_value(self) -> Optional[float]:
        return self._sma.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def grid_lines(self) -> Optional[np.ndarray]:
        return self._grid_lines

    @property
    def current_level(self) -> int:
        return self._current_level

    @property
    def peak_level(self) -> int:
        return self._peak_level

    @property
    def trough_level(self) -> int:
        return self._trough_level
