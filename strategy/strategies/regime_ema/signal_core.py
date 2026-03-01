"""Shared Regime EMA signal core for backtest and live trading.

This module contains RegimeEMASignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingADX, StreamingATR, StreamingEMA
from strategy.strategies.regime_ema.core import MarketRegime, RegimeEMAConfig, classify_regime


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class RegimeEMASignalCore:
    """Shared signal logic for Regime EMA backtest and live trading.

    EMA crossover gated on market regime:
    - Only allows entries in TRENDING_UP / TRENDING_DOWN regimes
    - Auto-closes positions when regime switches to RANGING
    - Regime detected via ATR ratio + ADX threshold
    """

    def __init__(
        self,
        config: RegimeEMAConfig,
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
        self._atr = StreamingATR(config.atr_period)
        self._adx = StreamingADX(config.adx_period)
        self._atr_mean_window: deque[float] = deque(maxlen=config.regime_lookback)

        # Previous EMA values for crossover detection
        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None

        # Current regime
        self._regime: MarketRegime = MarketRegime.RANGING

        # Position management state
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def update_indicators_only(self, close: float, high: float, low: float) -> None:
        """Update all indicators without generating a trading signal."""
        self._prev_ema_fast = self._ema_fast.value
        self._prev_ema_slow = self._ema_slow.value
        self._ema_fast.update(close)
        self._ema_slow.update(close)
        atr_val = self._atr.update(high, low, close)
        self._adx.update(high, low, close)
        if atr_val is not None:
            self._atr_mean_window.append(atr_val)
        self._update_regime()
        self.bar_index += 1

    def _update_regime(self) -> None:
        """Classify the current market regime."""
        atr_val = self._atr.value
        adx_val = self._adx.value
        ema_f = self._ema_fast.value
        ema_s = self._ema_slow.value

        if (
            atr_val is None
            or adx_val is None
            or ema_f is None
            or ema_s is None
            or len(self._atr_mean_window) < 5
        ):
            self._regime = MarketRegime.RANGING
            return

        atr_mean = float(np.mean(list(self._atr_mean_window)))
        self._regime = classify_regime(
            atr_val=atr_val,
            atr_mean=atr_mean,
            adx_val=adx_val,
            fast_ema=ema_f,
            slow_ema=ema_s,
            trend_atr_threshold=self._config.trend_atr_threshold,
            ranging_atr_threshold=self._config.ranging_atr_threshold,
            adx_trend_threshold=self._config.adx_trend_threshold,
        )

    def update(self, close: float, high: float, low: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        # Save previous EMA values
        self._prev_ema_fast = self._ema_fast.value
        self._prev_ema_slow = self._ema_slow.value

        # Update indicators
        ema_f = self._ema_fast.update(close)
        ema_s = self._ema_slow.update(close)
        atr_val = self._atr.update(high, low, close)
        adx_val = self._adx.update(high, low, close)
        if atr_val is not None:
            self._atr_mean_window.append(atr_val)

        self.bar_index += 1
        i = self.bar_index

        price = close

        # Skip if indicators not ready
        if (
            ema_f is None
            or ema_s is None
            or atr_val is None
            or adx_val is None
            or self._prev_ema_fast is None
            or self._prev_ema_slow is None
        ):
            return HOLD

        # Need enough ATR history for regime classification
        if len(self._atr_mean_window) < 5:
            return HOLD

        atr_mean = float(np.mean(list(self._atr_mean_window)))

        # Classify regime
        regime = classify_regime(
            atr_val=atr_val,
            atr_mean=atr_mean,
            adx_val=adx_val,
            fast_ema=ema_f,
            slow_ema=ema_s,
            trend_atr_threshold=self._config.trend_atr_threshold,
            ranging_atr_threshold=self._config.ranging_atr_threshold,
            adx_trend_threshold=self._config.adx_trend_threshold,
        )
        self._regime = regime

        is_trending = regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)

        # ---- Regime filter: auto-close in ranging ----
        if not is_trending and self.position != 0:
            if i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.entry_bar = i
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            return HOLD

        if not is_trending:
            self.signal_count[BUY] = 0
            self.signal_count[SELL] = 0
            return HOLD

        # ---- Stop loss check ----
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
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- EMA crossover signal ----
        prev_diff = self._prev_ema_fast - self._prev_ema_slow
        curr_diff = ema_f - ema_s

        raw_signal = HOLD
        if prev_diff <= 0 and curr_diff > 0:
            raw_signal = BUY
        elif prev_diff >= 0 and curr_diff < 0:
            raw_signal = SELL

        # ---- Cooldown check ----
        if i < self.cooldown_until:
            return HOLD

        # ---- Signal confirmation ----
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

        # ---- Position management ----
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
        """Reset all state."""
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._atr.reset()
        self._adx.reset()
        self._atr_mean_window.clear()
        self._prev_ema_fast = None
        self._prev_ema_slow = None
        self._regime = MarketRegime.RANGING
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0

    # ---- Indicator value properties ----

    @property
    def ema_fast_value(self) -> Optional[float]:
        return self._ema_fast.value

    @property
    def ema_slow_value(self) -> Optional[float]:
        return self._ema_slow.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def adx_value(self) -> Optional[float]:
        return self._adx.value

    @property
    def regime(self) -> MarketRegime:
        return self._regime

    @property
    def is_trending(self) -> bool:
        return self._regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)

    def get_raw_signal(self) -> int:
        """Compute raw crossover signal from current indicator values."""
        ema_f = self._ema_fast.value
        ema_s = self._ema_slow.value
        prev_f = self._prev_ema_fast
        prev_s = self._prev_ema_slow

        if any(v is None for v in (ema_f, ema_s, prev_f, prev_s)):
            return HOLD

        if not self.is_trending:
            return CLOSE

        prev_diff = prev_f - prev_s
        curr_diff = ema_f - ema_s

        if prev_diff <= 0 and curr_diff > 0:
            return BUY
        elif prev_diff >= 0 and curr_diff < 0:
            return SELL
        return HOLD
