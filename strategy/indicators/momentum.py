"""Shared Momentum signal core for backtest and live trading.

This module contains MomentumSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.indicators.base import (
    StreamingADX,
    StreamingATR,
    StreamingEMA,
    StreamingROC,
    StreamingSMA,
)

if TYPE_CHECKING:
    from strategy.strategies.momentum.core import MomentumConfig


# Signal constants matching nexustrader.backtest.Signal
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class MomentumSignalCore:
    """Shared signal logic for backtest and live trading.

    Processes OHLCV bars one at a time and returns a trading signal.
    Contains all indicator calculations, entry/exit conditions, and position
    management in a single class — the single source of truth.

    Usage (backtest):
        core = MomentumSignalCore(config, filter_config)
        for i in range(n):
            signal = core.update(close[i], high[i], low[i], volume[i])

    Usage (live indicator):
        core = MomentumSignalCore(config, filter_config)
        # On each confirmed kline:
        core.update_indicators_only(close, high, low, volume)
        # Read indicator values via properties
    """

    def __init__(
        self,
        config: MomentumConfig,
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
        self._ema_fast = StreamingEMA(config.ema_fast)
        self._ema_slow = StreamingEMA(config.ema_slow)
        self._ema_trend = StreamingEMA(config.ema_trend)
        self._atr = StreamingATR(config.atr_period)
        self._roc = StreamingROC(config.roc_period)
        self._vol_sma = StreamingSMA(config.volume_sma_period)
        self._adx = StreamingADX(config.adx_period)

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.trailing_stop = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def update_indicators_only(
        self, close: float, high: float, low: float, volume: float
    ) -> None:
        """Update all indicators without generating a trading signal.

        Used by the live indicator wrapper where position management
        is handled separately by the strategy class.
        """
        self._ema_fast.update(close)
        self._ema_slow.update(close)
        self._ema_trend.update(close)
        self._atr.update(high, low, close)
        self._roc.update(close)
        self._vol_sma.update(volume)
        self._adx.update(high, low, close)
        self.bar_index += 1

    def update(self, close: float, high: float, low: float, volume: float) -> int:
        """Process one bar and return a trading signal.

        Updates all indicators, then runs the full signal logic
        (exits, entries, position management).

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        # Update indicators
        ema_f = self._ema_fast.update(close)
        ema_s = self._ema_slow.update(close)
        ema_t = self._ema_trend.update(close)
        current_atr = self._atr.update(high, low, close)
        current_roc = self._roc.update(close)
        vol_sma = self._vol_sma.update(volume)
        current_adx = self._adx.update(high, low, close)

        self.bar_index += 1
        i = self.bar_index

        # Skip if any indicator is not ready
        if any(
            v is None for v in (current_roc, ema_f, ema_s, ema_t, current_atr, vol_sma)
        ):
            return HOLD

        vol_ok = volume > vol_sma * self._config.volume_threshold
        adx_val = current_adx if current_adx is not None else 0.0
        is_trending = adx_val >= self._config.adx_trend_threshold

        price = close

        # ---- 0. Regime filter: close positions in ranging market ----
        if not is_trending and self.position != 0:
            if i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.trailing_stop = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- 1. Exit / stop-loss checks ----
        if self.position != 0 and self.entry_price > 0:
            is_long = self.position == 1

            # Hard stop loss
            if is_long:
                pnl_pct = (price - self.entry_price) / self.entry_price
            else:
                pnl_pct = (self.entry_price - price) / self.entry_price

            if pnl_pct < -self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self.trailing_stop = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

            # ATR trailing stop (symmetric for long and short)
            if is_long:
                new_stop = price - current_atr * self._config.atr_multiplier
                self.trailing_stop = max(self.trailing_stop, new_stop)
                if price < self.trailing_stop:
                    self.position = 0
                    self.entry_price = 0.0
                    self.trailing_stop = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE
            else:
                new_stop = price + current_atr * self._config.atr_multiplier
                if self.trailing_stop > 0:
                    self.trailing_stop = min(self.trailing_stop, new_stop)
                else:
                    self.trailing_stop = new_stop
                if price > self.trailing_stop:
                    self.position = 0
                    self.entry_price = 0.0
                    self.trailing_stop = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE

            # Momentum/trend reversal exit
            if is_long:
                if current_roc < 0 or ema_f < ema_s:
                    if i - self.entry_bar >= self._min_holding_bars:
                        self.position = 0
                        self.entry_price = 0.0
                        self.trailing_stop = 0.0
                        self.cooldown_until = i + self._cooldown_bars
                        return CLOSE
            else:
                if current_roc > 0 or ema_f > ema_s:
                    if i - self.entry_bar >= self._min_holding_bars:
                        self.position = 0
                        self.entry_price = 0.0
                        self.trailing_stop = 0.0
                        self.cooldown_until = i + self._cooldown_bars
                        return CLOSE

        # ---- 2. Entry signal generation ----
        raw_signal = HOLD

        long_ok = (
            is_trending
            and current_roc > self._config.roc_threshold
            and ema_f > ema_s
            and price > ema_t
            and vol_ok
        )
        short_ok = (
            is_trending
            and current_roc < -self._config.roc_threshold
            and ema_f < ema_s
            and price < ema_t
            and vol_ok
        )

        if long_ok:
            raw_signal = BUY
        elif short_ok:
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
                self.trailing_stop = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = 1
                self.entry_bar = i
                self.entry_price = price
                self.trailing_stop = price - current_atr * self._config.atr_multiplier
                return BUY

        elif confirmed_signal == SELL:
            if self.position == 1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.trailing_stop = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = -1
                self.entry_bar = i
                self.entry_price = price
                self.trailing_stop = price + current_atr * self._config.atr_multiplier
                return SELL

        return HOLD

    def reset(self):
        """Reset all state (indicators + position management)."""
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._ema_trend.reset()
        self._atr.reset()
        self._roc.reset()
        self._vol_sma.reset()
        self._adx.reset()
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.trailing_stop = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    # ---- Indicator value properties (for live indicator wrapper) ----

    @property
    def roc_value(self) -> Optional[float]:
        return self._roc.value

    @property
    def ema_fast_value(self) -> Optional[float]:
        return self._ema_fast.value

    @property
    def ema_slow_value(self) -> Optional[float]:
        return self._ema_slow.value

    @property
    def ema_trend_value(self) -> Optional[float]:
        return self._ema_trend.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def vol_sma_value(self) -> Optional[float]:
        return self._vol_sma.value

    @property
    def adx_value(self) -> Optional[float]:
        return self._adx.value

    @property
    def is_trending(self) -> bool:
        adx = self._adx.value
        if adx is None:
            return False
        return adx >= self._config.adx_trend_threshold

    def get_raw_signal(self, price: float, volume: float) -> int:
        """Compute raw entry signal from current indicator values (no position management).

        Used by the live indicator wrapper to expose signal without
        modifying position state.
        """
        roc = self._roc.value
        ema_f = self._ema_fast.value
        ema_s = self._ema_slow.value
        ema_t = self._ema_trend.value
        vol_sma = self._vol_sma.value

        if any(v is None for v in (roc, ema_f, ema_s, ema_t, vol_sma)):
            return HOLD

        vol_ok = (
            volume > vol_sma * self._config.volume_threshold if vol_sma > 0 else False
        )

        long_ok = (
            self.is_trending
            and roc > self._config.roc_threshold
            and ema_f > ema_s
            and price > ema_t
            and vol_ok
        )
        short_ok = (
            self.is_trending
            and roc < -self._config.roc_threshold
            and ema_f < ema_s
            and price < ema_t
            and vol_ok
        )

        if long_ok:
            return BUY
        elif short_ok:
            return SELL
        return HOLD
