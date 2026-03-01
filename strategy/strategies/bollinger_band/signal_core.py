"""Shared Bollinger Band signal core for backtest and live trading.

This module contains BBSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.streaming import StreamingBB, StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.bollinger_band.core import BBConfig


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class BBSignalCore:
    """Shared signal logic for Bollinger Band backtest and live trading.

    Mean reversion strategy:
    - Price <= lower band -> BUY (oversold)
    - Price >= upper band -> SELL (overbought)
    - Price returns near SMA (distance_ratio < exit_threshold) -> CLOSE
    - Trend bias filter (auto/long_only/short_only) suppresses entry signals
    """

    def __init__(
        self,
        config: BBConfig,
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
        self._bb = StreamingBB(config.bb_period, config.bb_multiplier)

        # Trend SMA for auto bias detection
        trend_sma_len = config.bb_period * config.trend_sma_multiplier
        self._trend_sma = StreamingSMA(trend_sma_len)

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def update_indicators_only(self, close: float) -> None:
        """Update all indicators without generating a trading signal."""
        self._bb.update(close)
        self._trend_sma.update(close)
        self.bar_index += 1

    def update(self, close: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        sma, upper, lower = self._bb.update(close)
        trend_sma = self._trend_sma.update(close)

        self.bar_index += 1
        i = self.bar_index

        price = close

        # Skip if BB not ready
        if sma is None or upper is None or lower is None:
            return HOLD

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

        # ---- 2. Raw signal generation ----
        raw_signal = HOLD

        if price <= lower:
            raw_signal = BUY
        elif price >= upper:
            raw_signal = SELL
        elif self.position != 0:
            band_half = upper - sma
            if band_half > 1e-10:
                distance_ratio = abs(price - sma) / band_half
                if distance_ratio < self._config.exit_threshold:
                    raw_signal = CLOSE

        # ---- 2b. Apply trend bias filter ----
        effective_bias = self._config.trend_bias
        if self._config.trend_bias == "auto" and trend_sma is not None:
            effective_bias = "short_only" if price < trend_sma else "long_only"

        if effective_bias == "short_only" and raw_signal == BUY:
            raw_signal = HOLD
        elif effective_bias == "long_only" and raw_signal == SELL:
            raw_signal = HOLD

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
        self._bb.reset()
        self._trend_sma.reset()
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    # ---- Indicator value properties ----

    @property
    def sma_value(self) -> Optional[float]:
        return self._bb.sma

    @property
    def upper_value(self) -> Optional[float]:
        return self._bb.upper

    @property
    def lower_value(self) -> Optional[float]:
        return self._bb.lower

    @property
    def trend_sma_value(self) -> Optional[float]:
        return self._trend_sma.value

    def get_raw_signal(self, price: float) -> int:
        """Compute raw signal from current indicator values (no position management).

        Used by the live indicator wrapper to expose signal without
        modifying position state.
        """
        sma = self._bb.sma
        upper = self._bb.upper
        lower = self._bb.lower

        if sma is None or upper is None or lower is None:
            return HOLD

        signal = HOLD
        if price <= lower:
            signal = BUY
        elif price >= upper:
            signal = SELL
        elif sma is not None:
            band_half = upper - sma
            if band_half > 1e-10:
                distance_ratio = abs(price - sma) / band_half
                if distance_ratio < self._config.exit_threshold:
                    signal = CLOSE

        # Apply trend bias filter
        effective_bias = self._config.trend_bias
        trend_sma = self._trend_sma.value
        if self._config.trend_bias == "auto" and trend_sma is not None:
            effective_bias = "short_only" if price < trend_sma else "long_only"

        if effective_bias == "short_only" and signal == BUY:
            signal = HOLD
        elif effective_bias == "long_only" and signal == SELL:
            signal = HOLD

        return signal
