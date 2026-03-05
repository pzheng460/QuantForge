"""Shared Fear Reversal signal core for backtest and live trading.

This module contains FearReversalSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.streaming import (
    StreamingADX,
    StreamingATR,
    StreamingEMA,
    StreamingRSI,
    StreamingSMA,
)

if TYPE_CHECKING:
    from strategy.strategies.fear_reversal.core import FearReversalConfig


from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE


class FearReversalSignalCore(BaseSignalCore):
    """Shared signal logic for Fear Reversal backtest and live trading.

    Long-only strategy that enters when extreme fear creates a bounce
    opportunity. Uses a voting system of 5 indicators; requires
    min_signals confirmations to enter a long position.

    Usage (backtest):
        core = FearReversalSignalCore(config, filter_config)
        for i in range(n):
            signal = core.update(close[i], high[i], low[i], volume[i], open[i])

    Usage (live indicator):
        core = FearReversalSignalCore(config, filter_config)
        # During warmup:
        core.update_indicators_only(close, high, low, volume, open)
        # After warmup settles (enable_live_mode called by wrapper):
        core.update(close, high, low, volume, open)
    """

    def __init__(
        self,
        config: FearReversalConfig,
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
        self._rsi = StreamingRSI(config.rsi_period)
        self._atr = StreamingATR(config.atr_period)
        self._vol_sma = StreamingSMA(config.volume_sma_period)
        self._ema_support = StreamingEMA(config.ema_support_period)
        self._adx = StreamingADX(config.adx_period)

        # Indicator tracking state
        self._prev_rsi: Optional[float] = None  # for RSI cross detection
        self._last_open: float = 0.0  # stored for get_raw_signal

        # Position management state
        self.position = 0  # 0=flat, 1=long (no short in this strategy)
        self.entry_bar = 0
        self.entry_price = 0.0
        self.peak_price = 0.0  # highest close since entry (for trailing stop)
        self.cooldown_until = 0
        self.signal_count = {BUY: 0}
        self.bar_index = 0

    def _count_entry_signals(
        self,
        close: float,
        open_price: float,
        volume: float,
        current_rsi: Optional[float],
        current_atr: Optional[float],
    ) -> int:
        """Count how many of the 5 entry conditions are satisfied."""
        count = 0

        # Signal 1: RSI crossed back above oversold level (fear reversal)
        if (
            current_rsi is not None
            and self._prev_rsi is not None
            and self._prev_rsi < self._config.rsi_oversold
            and current_rsi >= self._config.rsi_oversold
        ):
            count += 1

        # Signal 2: Volume spike confirmation
        vol_sma = self._vol_sma.value
        if (
            vol_sma is not None
            and vol_sma > 0
            and volume > vol_sma * self._config.volume_threshold
        ):
            count += 1

        # Signal 3: Price above EMA dynamic support (EMA 200)
        ema_sup = self._ema_support.value
        if ema_sup is not None and close > ema_sup:
            count += 1

        # Signal 4: ADX weak (trend exhaustion / ranging — selling is exhausted)
        adx_val = self._adx.value
        if adx_val is None or adx_val < self._config.adx_weak_threshold:
            count += 1

        # Signal 5: Strong bullish momentum candle
        if (
            current_atr is not None
            and close > open_price
            and (close - open_price) > self._config.candle_atr_mult * current_atr
        ):
            count += 1

        return count

    def update_indicators_only(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        open: float,  # noqa: A002
    ) -> None:
        """Update all indicators without generating a trading signal.

        Used by the live indicator wrapper during warmup, where position
        management is handled separately.
        """
        self._prev_rsi = self._rsi.value
        self._rsi.update(close)
        self._atr.update(high, low, close)
        self._vol_sma.update(volume)
        self._ema_support.update(close)
        self._adx.update(high, low, close)
        self._last_open = open
        self.bar_index += 1

    def update(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        open: float,  # noqa: A002
    ) -> int:
        """Process one bar and return a trading signal.

        Updates all indicators, then runs the full signal logic
        (exits, entries, position management).

        Returns:
            Signal value: HOLD(0), BUY(1), or CLOSE(2).
        """
        # Update indicators (save prev RSI before update for cross detection)
        self._prev_rsi = self._rsi.value
        current_rsi = self._rsi.update(close)
        current_atr = self._atr.update(high, low, close)
        self._vol_sma.update(volume)
        self._ema_support.update(close)
        self._adx.update(high, low, close)
        self._last_open = open
        self.bar_index += 1
        i = self.bar_index

        # Skip until ATR is ready (proxy for all indicators having enough data)
        if current_atr is None:
            return HOLD

        # ---- 1. Exit checks (if in long position) ----
        if self.position == 1:
            # Update peak price for trailing stop
            self.peak_price = max(self.peak_price, close)

            # Exit 1: RSI overbought (take profit)
            if (
                current_rsi is not None
                and current_rsi > self._config.rsi_overbought
                and i - self.entry_bar >= self._min_holding_bars
            ):
                self.position = 0
                self.entry_price = 0.0
                self.peak_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

            # Exit 2: Hard stop loss (2% below entry)
            if self.entry_price > 0 and close < self.entry_price * (
                1 - self._config.stop_loss_pct
            ):
                self.position = 0
                self.entry_price = 0.0
                self.peak_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

            # Exit 3: ATR trailing stop from peak (only after min holding period)
            if self.entry_price > 0 and i - self.entry_bar >= self._min_holding_bars:
                trail_stop = self.peak_price - self._config.atr_trail_mult * current_atr
                if close < trail_stop:
                    self.position = 0
                    self.entry_price = 0.0
                    self.peak_price = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE

            # Exit 4: Time-based exit (max holding bars)
            if i - self.entry_bar >= self._config.max_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.peak_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- 2. Entry signal generation (flat position only) ----
        if i < self.cooldown_until or self.position != 0:
            return HOLD

        signal_count = self._count_entry_signals(
            close, open, volume, current_rsi, current_atr
        )
        raw_signal = BUY if signal_count >= self._config.min_signals else HOLD

        # ---- 3. Signal confirmation ----
        if raw_signal == BUY:
            self.signal_count[BUY] += 1
        else:
            self.signal_count[BUY] = 0

        if self.signal_count[BUY] >= self._signal_confirmation:
            # ---- 4. Enter long position ----
            self.position = 1
            self.entry_bar = i
            self.entry_price = close
            self.peak_price = close
            self.signal_count[BUY] = 0
            return BUY

        return HOLD

    def reset(self):
        """Reset all state (indicators + position management)."""
        self._rsi.reset()
        self._atr.reset()
        self._vol_sma.reset()
        self._ema_support.reset()
        self._adx.reset()
        self._prev_rsi = None
        self._last_open = 0.0
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.peak_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0}
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0
        self.peak_price = entry_price if pos_int != 0 else 0.0

    # ---- Indicator value properties (for live indicator wrapper) ----

    @property
    def rsi_value(self) -> Optional[float]:
        return self._rsi.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def vol_sma_value(self) -> Optional[float]:
        return self._vol_sma.value

    @property
    def ema_support_value(self) -> Optional[float]:
        return self._ema_support.value

    @property
    def adx_value(self) -> Optional[float]:
        return self._adx.value

    def get_signal_breakdown(self, close: float, volume: float) -> dict:
        """Return a dict with each signal's status for logging."""
        current_rsi = self._rsi.value
        current_atr = self._atr.value
        prev_rsi = self._prev_rsi
        open_price = self._last_open
        vol_sma = self._vol_sma.value
        ema_sup = self._ema_support.value
        adx_val = self._adx.value

        s1 = (prev_rsi is not None and current_rsi is not None
              and prev_rsi < self._config.rsi_oversold
              and current_rsi >= self._config.rsi_oversold)
        s2 = (vol_sma is not None and vol_sma > 0
              and volume > vol_sma * self._config.volume_threshold)
        s3 = ema_sup is not None and close > ema_sup
        s4 = adx_val is None or adx_val < self._config.adx_weak_threshold
        s5 = (current_atr is not None and close > open_price
              and (close - open_price) > self._config.candle_atr_mult * current_atr)

        return {
            "rsi": current_rsi,
            "prev_rsi": prev_rsi,
            "rsi_reversal": s1,
            "vol": volume,
            "vol_sma_x": vol_sma * self._config.volume_threshold if vol_sma else None,
            "vol_ok": s2,
            "price": close,
            "ema200": ema_sup,
            "above_ema": s3,
            "adx": adx_val,
            "adx_weak": s4,
            "candle_body": close - open_price if open_price else 0,
            "atr_thresh": self._config.candle_atr_mult * current_atr if current_atr else None,
            "strong_candle": s5,
            "count": sum([s1, s2, s3, s4, s5]),
            "needed": self._config.min_signals,
        }

    def get_raw_signal(self, close: float, volume: float) -> int:
        """Compute entry/exit signal from current indicator values.

        Used by the live indicator wrapper. Includes both entry AND exit
        logic since this strategy doesn't use dual-mode.
        """
        current_rsi = self._rsi.value
        current_atr = self._atr.value

        if current_atr is None:
            return HOLD

        # ---- Exit checks (if in long position) ----
        if self.position == 1:
            self.peak_price = max(self.peak_price, close)
            i = self.bar_index

            # Exit 1: RSI overbought
            if (current_rsi is not None
                    and current_rsi > self._config.rsi_overbought
                    and i - self.entry_bar >= self._min_holding_bars):
                return CLOSE

            # Exit 2: Hard stop loss
            if self.entry_price > 0 and close < self.entry_price * (1 - self._config.stop_loss_pct):
                return CLOSE

            # Exit 3: ATR trailing stop
            if self.entry_price > 0 and i - self.entry_bar >= self._min_holding_bars:
                trail_stop = self.peak_price - self._config.atr_trail_mult * current_atr
                if close < trail_stop:
                    return CLOSE

            # Exit 4: Max holding bars
            if i - self.entry_bar >= self._config.max_holding_bars:
                return CLOSE

            return HOLD

        # ---- Entry checks (flat position) ----
        signal_count = self._count_entry_signals(
            close, self._last_open, volume, current_rsi, current_atr
        )
        return BUY if signal_count >= self._config.min_signals else HOLD
