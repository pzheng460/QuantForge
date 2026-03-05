"""Shared EMA Crossover signal core for backtest and live trading.

This module contains EMASignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE
from strategy.strategies._base.streaming import StreamingEMA

if TYPE_CHECKING:
    from strategy.strategies.ema_crossover.core import EMAConfig


class EMASignalCore(BaseSignalCore):
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
        super().__init__(config, min_holding_bars, cooldown_bars, signal_confirmation)

        # Streaming indicators
        self._ema_fast = StreamingEMA(config.fast_period)
        self._ema_slow = StreamingEMA(config.slow_period)

        # Previous EMA values for crossover detection
        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None

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

        # Skip if any indicator is not ready
        if ema_f is None or ema_s is None:
            return HOLD
        if self._prev_ema_fast is None or self._prev_ema_slow is None:
            return HOLD

        price = close

        # ---- 1. Stop loss check ----
        sl = self._check_stop_loss(price)
        if sl is not None:
            return sl

        # ---- 2. Raw signal generation (crossover detection) ----
        prev_diff = self._prev_ema_fast - self._prev_ema_slow
        curr_diff = ema_f - ema_s

        raw_signal = HOLD
        if prev_diff <= 0 and curr_diff > 0:
            raw_signal = BUY
        elif prev_diff >= 0 and curr_diff < 0:
            raw_signal = SELL

        # ---- 3. Cooldown check ----
        if self.bar_index < self.cooldown_until:
            return HOLD

        # ---- 4. Signal confirmation + 5. Position management ----
        confirmed = self._confirm_signal(raw_signal)
        return self._apply_position_management(confirmed, price)

    def reset(self):
        """Reset all state (indicators + position management)."""
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._prev_ema_fast = None
        self._prev_ema_slow = None
        self._reset_position_state()

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
