"""Shared Dual Regime signal core for backtest and live trading.

This module contains DualRegimeSignalCore — a streaming, bar-by-bar signal
generator that switches between Momentum (trending) and Bollinger Band
(ranging) strategies based on ADX regime detection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.streaming import (
    StreamingADX,
    StreamingATR,
    StreamingBB,
    StreamingEMA,
    StreamingROC,
    StreamingSMA,
)

if TYPE_CHECKING:
    from strategy.strategies.dual_regime.core import DualRegimeConfig


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class DualRegimeSignalCore:
    """Shared signal logic for Dual Regime backtest and live trading.

    Switches between:
    - Momentum strategy (ADX >= threshold): ROC + EMA + Volume
    - Bollinger Band mean reversion (ADX < threshold): Band touches
    Regime switches cause position closure.
    """

    def __init__(
        self,
        config: DualRegimeConfig,
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
        self._adx = StreamingADX(config.adx_period)
        self._roc = StreamingROC(config.roc_period)
        self._ema_fast = StreamingEMA(config.ema_fast)
        self._ema_slow = StreamingEMA(config.ema_slow)
        self._ema_trend = StreamingEMA(config.ema_trend)
        self._atr = StreamingATR(config.atr_period)
        self._vol_sma = StreamingSMA(config.volume_sma_period)
        self._bb = StreamingBB(config.bb_period, config.bb_std)

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.trailing_stop = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0
        self._current_regime: Optional[str] = None  # 'momentum' or 'bb'

    def update_indicators_only(
        self, close: float, high: float, low: float, volume: float
    ) -> None:
        """Update all indicators without generating a trading signal."""
        self._adx.update(high, low, close)
        self._roc.update(close)
        self._ema_fast.update(close)
        self._ema_slow.update(close)
        self._ema_trend.update(close)
        self._atr.update(high, low, close)
        self._vol_sma.update(volume)
        self._bb.update(close)
        self.bar_index += 1

    def update(self, close: float, high: float, low: float, volume: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        # Update all indicators
        current_adx = self._adx.update(high, low, close)
        current_roc = self._roc.update(close)
        ema_f = self._ema_fast.update(close)
        ema_s = self._ema_slow.update(close)
        ema_t = self._ema_trend.update(close)
        current_atr = self._atr.update(high, low, close)
        vol_sma = self._vol_sma.update(volume)
        bb_sma, bb_upper, bb_lower = self._bb.update(close)

        self.bar_index += 1
        i = self.bar_index

        price = close

        # Skip if critical indicators not ready
        if current_adx is None or bb_sma is None:
            return HOLD

        new_regime = (
            "momentum" if current_adx >= self._config.adx_trend_threshold else "bb"
        )

        # ---- 1. Regime Switch Detection ----
        if self._current_regime is not None and self._current_regime != new_regime:
            if self.position != 0:
                self.position = 0
                self.entry_price = 0.0
                self.trailing_stop = 0.0
                self.cooldown_until = i + self._cooldown_bars
                self._current_regime = new_regime
                return CLOSE
            self._current_regime = new_regime
            return HOLD

        self._current_regime = new_regime

        # ---- 2. Exit / stop-loss checks ----
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

            # Strategy-specific exits
            if self._current_regime == "momentum":
                if (
                    current_roc is None
                    or ema_f is None
                    or ema_s is None
                    or current_atr is None
                ):
                    return HOLD

                # ATR trailing stop
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
                    if self.trailing_stop == 0.0:
                        self.trailing_stop = new_stop
                    else:
                        self.trailing_stop = min(self.trailing_stop, new_stop)
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

            elif self._current_regime == "bb":
                middle = bb_sma
                if is_long and price >= middle:
                    if i - self.entry_bar >= self._min_holding_bars:
                        self.position = 0
                        self.entry_price = 0.0
                        self.trailing_stop = 0.0
                        self.cooldown_until = i + self._cooldown_bars
                        return CLOSE
                elif not is_long and price <= middle:
                    if i - self.entry_bar >= self._min_holding_bars:
                        self.position = 0
                        self.entry_price = 0.0
                        self.trailing_stop = 0.0
                        self.cooldown_until = i + self._cooldown_bars
                        return CLOSE

        # ---- 3. Entry signal generation ----
        raw_signal = HOLD

        if self._current_regime == "momentum":
            if (
                current_roc is None
                or ema_f is None
                or ema_s is None
                or ema_t is None
                or vol_sma is None
            ):
                return HOLD

            vol_ok = volume > vol_sma * self._config.volume_threshold

            long_ok = (
                current_roc > self._config.roc_threshold
                and ema_f > ema_s
                and price > ema_t
                and vol_ok
            )
            short_ok = (
                current_roc < -self._config.roc_threshold
                and ema_f < ema_s
                and price < ema_t
                and vol_ok
            )

            if long_ok:
                raw_signal = BUY
            elif short_ok:
                raw_signal = SELL

        elif self._current_regime == "bb":
            if bb_upper is not None and bb_lower is not None:
                if price < bb_lower:
                    raw_signal = BUY
                elif price > bb_upper:
                    raw_signal = SELL

        # ---- 4. Cooldown check ----
        if i < self.cooldown_until:
            return HOLD

        # ---- 5. Signal confirmation ----
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

        # ---- 6. Position management ----
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
                if self._current_regime == "momentum" and current_atr is not None:
                    self.trailing_stop = (
                        price - current_atr * self._config.atr_multiplier
                    )
                else:
                    self.trailing_stop = 0.0
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
                if self._current_regime == "momentum" and current_atr is not None:
                    self.trailing_stop = (
                        price + current_atr * self._config.atr_multiplier
                    )
                else:
                    self.trailing_stop = 0.0
                return SELL

        return HOLD

    def reset(self):
        """Reset all state."""
        self._adx.reset()
        self._roc.reset()
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._ema_trend.reset()
        self._atr.reset()
        self._vol_sma.reset()
        self._bb.reset()
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.trailing_stop = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0
        self._current_regime = None

    # ---- Indicator value properties ----

    @property
    def adx_value(self) -> Optional[float]:
        return self._adx.value

    @property
    def current_regime(self) -> Optional[str]:
        return self._current_regime

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
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def bb_sma(self) -> Optional[float]:
        return self._bb.sma

    @property
    def bb_upper(self) -> Optional[float]:
        return self._bb.upper

    @property
    def bb_lower(self) -> Optional[float]:
        return self._bb.lower
