"""
NexusTrader Indicator wrapper for Grid Trading strategy.

This module implements grid trading logic with dynamic SMA+ATR based grid calculation.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade


class GridSignal(Enum):
    """Grid trading signals."""

    HOLD = "hold"
    BUY = "buy"       # Buy when price goes down through entry lines
    SELL = "sell"     # Sell when price goes up through entry lines
    CLOSE = "close"   # Close position when profit target reached


class GridIndicator(Indicator):
    """
    Grid Trading Indicator that calculates SMA, ATR and dynamic grid lines.

    Grid Logic:
    - Center: SMA(20)
    - Range: ATR(14) * 3.0
    - Grid: 4 levels (0, 1, 2, 3) from lower to upper
    - Recalculate every 24 bars
    - Track current_level, peak_level, trough_level for signals

    Grid Trading Signals:
    - FLAT -> BUY when price drops >= entry_lines AND current_level <= grid_count//2
    - FLAT -> SELL when price rises >= entry_lines AND current_level >= grid_count//2
    - LONG -> CLOSE when price rises >= profit_lines from trough
    - SHORT -> CLOSE when price falls >= profit_lines from peak
    """

    def __init__(
        self,
        grid_count: int = 3,
        atr_multiplier: float = 3.0,
        sma_period: int = 20,
        atr_period: int = 14,
        recalc_period: int = 24,
        entry_lines: int = 1,
        profit_lines: int = 2,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._grid_count = grid_count
        self._atr_multiplier = atr_multiplier
        self._sma_period = sma_period
        self._atr_period = atr_period
        self._recalc_period = recalc_period
        self._entry_lines = entry_lines
        self._profit_lines = profit_lines

        # Total grid levels = grid_count + 1 (0 to grid_count)
        self._total_levels = grid_count + 1

        # Calculate real warmup period (need enough data for SMA + ATR)
        self._real_warmup_period = max(sma_period, atr_period) + 10

        # Disable framework warmup - we manage it ourselves
        super().__init__(
            params={
                "grid_count": grid_count,
                "atr_multiplier": atr_multiplier,
                "sma_period": sma_period,
                "atr_period": atr_period,
            },
            name="Grid",
            warmup_period=None,
            kline_interval=kline_interval,
        )

        # Track confirmed bars
        self._confirmed_bar_count: int = 0

        # Price and volume history
        max_history = max(sma_period, atr_period) + 10
        self._close_history: deque[float] = deque(maxlen=max_history)
        self._high_history: deque[float] = deque(maxlen=atr_period + 2)
        self._low_history: deque[float] = deque(maxlen=atr_period + 2)

        # SMA state
        self._sma_val: Optional[float] = None

        # ATR state (using simple moving average method for simplicity)
        self._atr_val: Optional[float] = None
        self._tr_history: deque[float] = deque(maxlen=atr_period + 2)
        self._prev_close: Optional[float] = None

        # Grid state
        self._grid_lines: Optional[np.ndarray] = None
        self._grid_lower: Optional[float] = None
        self._grid_upper: Optional[float] = None
        self._bars_since_recalc: int = 0

        # Level tracking
        self._current_level: int = 0
        self._peak_level: int = 0      # Highest level reached in current cycle
        self._trough_level: int = 0    # Lowest level reached in current cycle

        # Signal
        self._signal: GridSignal = GridSignal.HOLD
        self._last_price: Optional[float] = None

        # When True, _recalculate_grid will NOT reset peak/trough
        # Strategy sets this when there's an open position
        self._preserve_peak_trough: bool = False

    @property
    def is_warmed_up(self) -> bool:
        """Check if enough confirmed bars have been processed."""
        return self._confirmed_bar_count >= self._real_warmup_period

    def handle_kline(self, kline: Kline) -> None:
        """Process new kline data using timestamp change detection for bar confirmation."""
        bar_start = int(kline.start)

        if not hasattr(self, '_current_bar_start'):
            # First kline: just store it
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
            return

        if bar_start != self._current_bar_start:
            # New bar started → previous bar is confirmed
            confirmed_kline = self._current_bar_kline
            self._confirmed_bar_count += 1
            self._process_kline_data(confirmed_kline)

            # Update to new bar
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
        else:
            # Same bar: update latest kline for real-time price
            self._current_bar_kline = kline
            # Update current level for real-time tracking
            if self._grid_lines is not None:
                self._update_current_level(float(kline.close))

    def _process_kline_data(self, kline: Kline) -> None:
        """Process confirmed kline data and update all indicators."""
        close = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        self._last_price = close

        # Update price history
        self._close_history.append(close)
        self._high_history.append(high)
        self._low_history.append(low)

        # Update SMA
        self._update_sma()

        # Update ATR
        self._update_atr(close, high, low)

        # Check if we need to recalculate grid
        self._bars_since_recalc += 1
        if self._bars_since_recalc >= self._recalc_period or self._grid_lines is None:
            self._recalculate_grid()
            self._bars_since_recalc = 0

        # Update current level and peak/trough tracking
        if self._grid_lines is not None:
            self._update_current_level(close)
            self._update_peak_trough()

        # Generate signal
        self._update_signal()

    def _update_sma(self) -> None:
        """Update Simple Moving Average."""
        closes = list(self._close_history)
        if len(closes) >= self._sma_period:
            self._sma_val = float(np.mean(closes[-self._sma_period:]))

    def _update_atr(self, close: float, high: float, low: float) -> None:
        """Update Average True Range using simple moving average."""
        # Calculate True Range
        if self._prev_close is not None:
            hl = high - low
            hc = abs(high - self._prev_close)
            lc = abs(low - self._prev_close)
            tr = max(hl, hc, lc)
        else:
            tr = high - low

        self._tr_history.append(tr)
        self._prev_close = close

        # Calculate ATR as simple moving average of True Range
        tr_list = list(self._tr_history)
        if len(tr_list) >= self._atr_period:
            self._atr_val = float(np.mean(tr_list[-self._atr_period:]))

    def _recalculate_grid(self) -> None:
        """Recalculate grid lines based on current SMA and ATR."""
        if self._sma_val is None or self._atr_val is None:
            return

        center = self._sma_val
        range_size = self._atr_val * self._atr_multiplier

        self._grid_lower = center - range_size / 2
        self._grid_upper = center + range_size / 2

        # Create grid levels: 0 (bottom) to grid_count (top)
        self._grid_lines = np.linspace(self._grid_lower, self._grid_upper, self._total_levels)

        # Update current level but do NOT reset peak/trough if has_position flag is set
        # (caller manages this flag via preserve_peak_trough)
        current_price = self._last_price or center
        self._update_current_level(current_price)
        if not self._preserve_peak_trough:
            self._peak_level = self._current_level
            self._trough_level = self._current_level
        else:
            # Still update peak/trough based on new level
            self._peak_level = max(self._peak_level, self._current_level)
            self._trough_level = min(self._trough_level, self._current_level)

    def _update_current_level(self, price: float) -> None:
        """Update current grid level based on price."""
        if self._grid_lines is None:
            return

        # Find which grid level the price is at
        for i in range(len(self._grid_lines) - 1):
            if price <= self._grid_lines[i + 1]:
                self._current_level = i
                return
        # Price above highest grid
        self._current_level = len(self._grid_lines) - 1

    def _update_peak_trough(self) -> None:
        """Update peak and trough levels."""
        self._peak_level = max(self._peak_level, self._current_level)
        self._trough_level = min(self._trough_level, self._current_level)

    def _update_signal(self) -> None:
        """Generate trading signal based on grid levels and movement."""
        if not self.is_warmed_up or self._grid_lines is None:
            self._signal = GridSignal.HOLD
            return

        # For now, just set to HOLD - actual trading logic will be in strategy.py
        # The strategy will use current_level, peak_level, trough_level to make decisions
        self._signal = GridSignal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # Properties
    @property
    def value(self) -> dict:
        return {
            "sma": self._sma_val,
            "atr": self._atr_val,
            "current_level": self._current_level,
            "peak_level": self._peak_level,
            "trough_level": self._trough_level,
            "grid_lower": self._grid_lower,
            "grid_upper": self._grid_upper,
            "signal": self._signal.value,
        }

    @property
    def sma(self) -> Optional[float]:
        return self._sma_val

    @property
    def atr(self) -> Optional[float]:
        return self._atr_val

    @property
    def grid_lines(self) -> Optional[np.ndarray]:
        return self._grid_lines

    @property
    def grid_lower(self) -> Optional[float]:
        return self._grid_lower

    @property
    def grid_upper(self) -> Optional[float]:
        return self._grid_upper

    @property
    def current_level(self) -> int:
        return self._current_level

    @property
    def peak_level(self) -> int:
        return self._peak_level

    @property
    def trough_level(self) -> int:
        return self._trough_level

    @property
    def grid_count(self) -> int:
        return self._grid_count

    @property
    def entry_lines(self) -> int:
        return self._entry_lines

    @property
    def profit_lines(self) -> int:
        return self._profit_lines

    def get_signal(self) -> GridSignal:
        return self._signal

    def reset(self) -> None:
        """Reset all indicator state."""
        self._close_history.clear()
        self._high_history.clear()
        self._low_history.clear()
        self._tr_history.clear()
        self._sma_val = None
        self._atr_val = None
        self._prev_close = None
        self._grid_lines = None
        self._grid_lower = None
        self._grid_upper = None
        self._bars_since_recalc = 0
        self._current_level = 0
        self._peak_level = 0
        self._trough_level = 0
        self._signal = GridSignal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, '_current_bar_start'):
            del self._current_bar_start
        if hasattr(self, '_current_bar_kline'):
            del self._current_bar_kline
        self._preserve_peak_trough = False