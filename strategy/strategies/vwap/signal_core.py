"""Shared VWAP signal core for backtest and live trading.

This module contains VWAPSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingRSI

if TYPE_CHECKING:
    from strategy.strategies.vwap.core import VWAPConfig


from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE


class VWAPSignalCore(BaseSignalCore):
    """Shared signal logic for VWAP backtest and live trading.

    VWAP mean-reversion with RSI confirmation:
    - Z <= -entry AND RSI < oversold -> BUY
    - Z >= entry AND RSI > overbought -> SELL
    - |Z| < exit_threshold -> CLOSE
    - |Z| > zscore_stop OR pnl < -stop_loss_pct -> CLOSE (model failure)
    """

    def __init__(
        self,
        config: VWAPConfig,
        min_holding_bars: int = 4,
        cooldown_bars: int = 2,
        signal_confirmation: int = 1,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars
        self._signal_confirmation = signal_confirmation

        # Streaming RSI
        self._rsi = StreamingRSI(config.rsi_period)

        # VWAP cumulative state (resets daily)
        self._cum_tp_vol: float = 0.0
        self._cum_vol: float = 0.0
        self._current_day: Optional[object] = None

        # History for Z-score rolling window
        self._close_history: deque[float] = deque(maxlen=config.std_window + 50)
        self._vwap_history: deque[float] = deque(maxlen=config.std_window + 50)

        # Current indicator values
        self._vwap: Optional[float] = None
        self._zscore: float = 0.0
        self._rsi_value: float = 50.0

        # Position management state
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def update_indicators_only(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        day: Optional[object] = None,
    ) -> None:
        """Update all indicators without generating a trading signal."""
        self._update_vwap(close, high, low, volume, day)
        self._update_zscore(close)
        rsi_val = self._rsi.update(close)
        if rsi_val is not None:
            self._rsi_value = rsi_val
        self.bar_index += 1

    def _update_vwap(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        day: Optional[object] = None,
    ) -> None:
        """Update VWAP with daily reset."""
        if (
            day is not None
            and self._current_day is not None
            and day != self._current_day
        ):
            self._cum_tp_vol = 0.0
            self._cum_vol = 0.0
        self._current_day = day

        typical_price = (high + low + close) / 3.0
        if volume > 0:
            self._cum_tp_vol += typical_price * volume
            self._cum_vol += volume
        if self._cum_vol > 0:
            self._vwap = self._cum_tp_vol / self._cum_vol

        self._close_history.append(close)
        if self._vwap is not None:
            self._vwap_history.append(self._vwap)

    def _update_zscore(self, close: float) -> None:
        """Update rolling Z-score of price deviation from VWAP."""
        if (
            len(self._close_history) >= self._config.std_window
            and len(self._vwap_history) >= self._config.std_window
        ):
            recent_closes = np.array(
                list(self._close_history)[-self._config.std_window :]
            )
            recent_vwap = np.array(list(self._vwap_history)[-self._config.std_window :])
            deviations = recent_closes - recent_vwap
            std = float(np.std(deviations, ddof=0))
            if std > 1e-10 and self._vwap is not None:
                self._zscore = (close - self._vwap) / std
            else:
                self._zscore = 0.0

    def update(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        day: Optional[object] = None,
    ) -> int:
        """Process one bar and return a trading signal.

        Args:
            close: Close price.
            high: High price.
            low: Low price.
            volume: Volume.
            day: Day identifier for VWAP reset (e.g. date object).

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        # Update VWAP
        self._update_vwap(close, high, low, volume, day)

        # Update zscore
        self._update_zscore(close)

        # Update RSI
        rsi_val = self._rsi.update(close)
        if rsi_val is not None:
            self._rsi_value = rsi_val

        self.bar_index += 1
        i = self.bar_index

        price = close
        z = self._zscore
        r = self._rsi_value

        # Need enough data
        if self._vwap is None or rsi_val is None:
            return HOLD
        if len(self._close_history) < self._config.std_window:
            return HOLD

        # ---- 1. Stop loss check ----
        if self.position != 0 and self.entry_price > 0:
            is_long = self.position == 1
            if is_long:
                pnl_pct = (price - self.entry_price) / self.entry_price
            else:
                pnl_pct = (self.entry_price - price) / self.entry_price

            if (
                abs(z) >= self._config.zscore_stop
                or pnl_pct < -self._config.stop_loss_pct
            ):
                self.position = 0
                self.entry_price = 0.0
                self.entry_bar = i
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- 2. Raw signal generation ----
        raw_signal = HOLD

        if z <= -self._config.zscore_entry and r < self._config.rsi_oversold:
            raw_signal = BUY
        elif z >= self._config.zscore_entry and r > self._config.rsi_overbought:
            raw_signal = SELL
        elif self.position != 0 and abs(z) <= abs(self._config.zscore_exit):
            raw_signal = CLOSE

        # ---- 3. Cooldown check (allow CLOSE during cooldown) ----
        if i < self.cooldown_until:
            if raw_signal == CLOSE and self.position != 0:
                if i - self.entry_bar >= self._min_holding_bars:
                    self.position = 0
                    self.entry_price = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE
            return HOLD

        # ---- 4. Signal confirmation ----
        if raw_signal == BUY:
            self.signal_count[BUY] += 1
            self.signal_count[SELL] = 0
        elif raw_signal == SELL:
            self.signal_count[SELL] += 1
            self.signal_count[BUY] = 0
        else:
            self.signal_count[BUY] = 0
            self.signal_count[SELL] = 0

        confirmed_signal = raw_signal
        if raw_signal in (BUY, SELL):
            if raw_signal == BUY:
                if self.signal_count[BUY] < self._signal_confirmation:
                    confirmed_signal = HOLD
            elif raw_signal == SELL:
                if self.signal_count[SELL] < self._signal_confirmation:
                    confirmed_signal = HOLD

        # ---- 5. Position management ----
        if confirmed_signal == CLOSE and self.position != 0:
            if i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        elif confirmed_signal == BUY:
            if self.position == -1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = 1
                self.entry_bar = i
                self.entry_price = price
                return BUY

        elif confirmed_signal == SELL:
            if self.position == 1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = -1
                self.entry_bar = i
                self.entry_price = price
                return SELL

        return HOLD

    def reset(self):
        """Reset all state."""
        self._rsi.reset()
        self._cum_tp_vol = 0.0
        self._cum_vol = 0.0
        self._current_day = None
        self._close_history.clear()
        self._vwap_history.clear()
        self._vwap = None
        self._zscore = 0.0
        self._rsi_value = 50.0
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0


    # ---- Indicator value properties ----

    @property
    def vwap_value(self) -> Optional[float]:
        return self._vwap

    @property
    def zscore_value(self) -> float:
        return self._zscore

    @property
    def rsi_value(self) -> float:
        return self._rsi_value

    def get_raw_signal(self) -> int:
        """Compute raw signal from current indicator values (no position management)."""
        if self._vwap is None:
            return HOLD

        z = self._zscore
        r = self._rsi_value

        if z <= -self._config.zscore_entry and r < self._config.rsi_oversold:
            return BUY
        elif z >= self._config.zscore_entry and r > self._config.rsi_overbought:
            return SELL
        elif abs(z) <= abs(self._config.zscore_exit):
            return CLOSE
        return HOLD
