"""Base class for all SignalCore implementations.

Provides common position management state and helper methods shared
across all signal cores. Subclasses implement update(),
update_indicators_only(), get_raw_signal(), and reset() for their
specific signal logic.
"""

from __future__ import annotations

# Signal constants — also available as class attributes (self.BUY, etc.)
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class BaseSignalCore:
    """Base class for all SignalCore implementations.

    Provides:
    - Signal constants as class attributes (HOLD, BUY, SELL, CLOSE)
    - Position management state (position, entry_bar, entry_price,
      cooldown_until, signal_count, bar_index)
    - sync_position() for external state synchronisation
    - _reset_position_state() to reset position state in reset()
    - _check_stop_loss(price) for standard PnL-based stop loss
    - _confirm_signal(raw_signal) for signal confirmation with counts
    - _apply_position_management(confirmed_signal, price) for standard PM
    """

    # Signal constants
    HOLD = 0
    BUY = 1
    SELL = -1
    CLOSE = 2

    def __init__(
        self,
        config,
        min_holding_bars: int = 4,
        cooldown_bars: int = 2,
        signal_confirmation: int = 1,
    ):
        self._config = config
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars
        self._signal_confirmation = signal_confirmation

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0

    def _reset_position_state(self) -> None:
        """Reset position management state to initial values.

        Call this in subclass reset() after resetting indicator state.
        """
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def _check_stop_loss(self, price: float) -> int | None:
        """Check standard PnL-based stop loss condition.

        Returns CLOSE and resets position state if stop loss triggered,
        otherwise returns None. For strategies with additional stop
        conditions (e.g. z-score), keep the stop loss block inline.
        """
        if self.position != 0 and self.entry_price > 0:
            is_long = self.position == 1
            if is_long:
                pnl_pct = (price - self.entry_price) / self.entry_price
            else:
                pnl_pct = (self.entry_price - price) / self.entry_price
            if pnl_pct < -self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self.entry_bar = self.bar_index
                return CLOSE
        return None

    def _confirm_signal(self, raw_signal: int) -> int:
        """Update signal confirmation counters and return confirmed signal.

        BUY/SELL require _signal_confirmation consecutive matching bars.
        HOLD and CLOSE pass through immediately; counters reset on
        non-BUY/SELL raw signals.
        """
        if raw_signal == BUY:
            self.signal_count[BUY] += 1
            self.signal_count[SELL] = 0
            return BUY if self.signal_count[BUY] >= self._signal_confirmation else HOLD
        elif raw_signal == SELL:
            self.signal_count[SELL] += 1
            self.signal_count[BUY] = 0
            return SELL if self.signal_count[SELL] >= self._signal_confirmation else HOLD
        else:
            self.signal_count[BUY] = 0
            self.signal_count[SELL] = 0
            return raw_signal  # HOLD or CLOSE passes through

    def _apply_position_management(self, confirmed_signal: int, price: float) -> int:
        """Apply standard BUY/SELL/CLOSE position management.

        - BUY: open long when flat; close short (+ cooldown) when min_holding met
        - SELL: open short when flat; close long (+ cooldown) when min_holding met
        - CLOSE: close any position (+ cooldown) when min_holding met

        Returns the signal actually executed, or HOLD if no action taken.
        """
        i = self.bar_index
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
        elif confirmed_signal == CLOSE:
            if self.position != 0 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
        return HOLD
