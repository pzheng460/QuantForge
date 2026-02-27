"""Shared EMA Crossover signal core for backtest and live trading.

This module contains EMASignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.indicators.base import StreamingEMA

if TYPE_CHECKING:
    from strategy.strategies.ema_crossover.core import EMAConfig


# Signal constants matching nexustrader.backtest.Signal
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class EMASignalCore:
    """Shared signal logic for EMA Crossover backtest and live trading.

    Processes OHLCV bars one at a time and returns a trading signal.
    Contains all indicator calculations, entry/exit conditions, and position
    management in a single class — the single source of truth.
    """

    def __init__(
        self,
        config: EMAConfig,
        min_holding_bars: int = 4,
        cooldown_bars: int = 2,
        signal_confirmation: int = 1,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars
        self._signal_confirmation = signal_confirmation

        # Streaming indicators
        self._ema_fast = StreamingEMA(config.fast_period)
        self._ema_slow = StreamingEMA(config.slow_period)

        # Previous EMA values for crossover detection
        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def update_indicators_only(self, close: float) -> None:
        """Update all indicators without generating a trading signal.

        Used by the live indicator wrapper where position management
        is handled separately by the strategy class.
        """
        self._prev_ema_fast = self._ema_fast.value
        self._prev_ema_slow = self._ema_slow.value
        self._ema_fast.update(close)
        self._ema_slow.update(close)
        self.bar_index += 1

    def update(self, close: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        # Save previous values for crossover detection
        self._prev_ema_fast = self._ema_fast.value
        self._prev_ema_slow = self._ema_slow.value

        # Update indicators
        ema_f = self._ema_fast.update(close)
        ema_s = self._ema_slow.update(close)

        self.bar_index += 1
        i = self.bar_index

        # Skip if any indicator is not ready
        if ema_f is None or ema_s is None:
            return HOLD
        if self._prev_ema_fast is None or self._prev_ema_slow is None:
            return HOLD

        price = close

        # ---- 1. Stop loss check ----
        if self.position != 0 and self.entry_price > 0:
            is_long = self.position == 1
            if is_long:
                pnl_pct = (price - self.entry_price) / self.entry_price
            else:
                pnl_pct = (self.entry_price - price) / self.entry_price

            if pnl_pct < -self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self.entry_bar = i
                return CLOSE

        # ---- 2. Raw signal generation (crossover detection) ----
        prev_diff = self._prev_ema_fast - self._prev_ema_slow
        curr_diff = ema_f - ema_s

        raw_signal = HOLD

        if prev_diff <= 0 and curr_diff > 0:
            raw_signal = BUY
        elif prev_diff >= 0 and curr_diff < 0:
            raw_signal = SELL

        # ---- 3. Cooldown check ----
        if i < self.cooldown_until:
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

        confirmed_signal = HOLD
        if self.signal_count[BUY] >= self._signal_confirmation:
            confirmed_signal = BUY
        elif self.signal_count[SELL] >= self._signal_confirmation:
            confirmed_signal = SELL

        # ---- 5. Position management ----
        if confirmed_signal == BUY:
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
        """Reset all state (indicators + position management)."""
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._prev_ema_fast = None
        self._prev_ema_slow = None
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    # ---- Indicator value properties (for live indicator wrapper) ----

    @property
    def ema_fast_value(self) -> Optional[float]:
        return self._ema_fast.value

    @property
    def ema_slow_value(self) -> Optional[float]:
        return self._ema_slow.value

    @property
    def prev_ema_fast_value(self) -> Optional[float]:
        return self._prev_ema_fast

    @property
    def prev_ema_slow_value(self) -> Optional[float]:
        return self._prev_ema_slow

    def get_raw_signal(self) -> int:
        """Compute raw crossover signal from current indicator values.

        Used by the live indicator wrapper to expose signal without
        modifying position state.
        """
        ema_f = self._ema_fast.value
        ema_s = self._ema_slow.value
        prev_f = self._prev_ema_fast
        prev_s = self._prev_ema_slow

        if any(v is None for v in (ema_f, ema_s, prev_f, prev_s)):
            return HOLD

        prev_diff = prev_f - prev_s
        curr_diff = ema_f - ema_s

        if prev_diff <= 0 and curr_diff > 0:
            return BUY
        elif prev_diff >= 0 and curr_diff < 0:
            return SELL
        return HOLD
