"""
Multi-Timeframe Momentum Signal Generator.

Exchange-agnostic vectorised signal generator for the backtest framework.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.momentum.core import (
    MomentumConfig,
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_roc,
    calculate_sma,
)


@dataclass
class MomentumTradeFilterConfig:
    """Configuration for trade filtering (Momentum-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class MomentumSignalGenerator:
    """Generate trading signals for vectorised backtest.

    Entry (long, all must hold):
        1. ROC > roc_threshold
        2. EMA_fast > EMA_slow
        3. Price > EMA_trend
        4. Volume > volume_SMA * volume_threshold

    Entry (short) is symmetric:
        1. ROC < -roc_threshold
        2. EMA_fast < EMA_slow
        3. Price < EMA_trend
        4. Volume > volume_SMA * volume_threshold

    Exit (any one):
        1. ROC reversal (long: ROC < 0; short: ROC > 0)
        2. EMA crossover reversal
        3. ATR trailing stop  OR  hard stop_loss_pct
    """

    def __init__(
        self,
        config: MomentumConfig,
        filter_config: MomentumTradeFilterConfig,
    ):
        self.config = config
        self.filter = filter_config

    def generate(
        self, data: pd.DataFrame, params: Optional[Dict] = None
    ) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame (open, high, low, close, volume).
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
        """
        p = params or {}

        roc_period = int(p.get("roc_period", self.config.roc_period))
        roc_threshold = float(p.get("roc_threshold", self.config.roc_threshold))
        ema_fast = int(p.get("ema_fast", self.config.ema_fast))
        ema_slow = int(p.get("ema_slow", self.config.ema_slow))
        ema_trend = int(p.get("ema_trend", self.config.ema_trend))
        atr_period = int(p.get("atr_period", self.config.atr_period))
        atr_multiplier = float(p.get("atr_multiplier", self.config.atr_multiplier))
        volume_sma_period = int(p.get("volume_sma_period", self.config.volume_sma_period))
        volume_threshold = float(p.get("volume_threshold", self.config.volume_threshold))
        stop_loss_pct = float(p.get("stop_loss_pct", self.config.stop_loss_pct))
        adx_period = int(p.get("adx_period", self.config.adx_period))
        adx_trend_threshold = float(p.get("adx_trend_threshold", self.config.adx_trend_threshold))

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values
        volumes = data["volume"].values

        # Compute indicators
        roc = calculate_roc(prices, roc_period)
        ema_f = calculate_ema(prices, ema_fast)
        ema_s = calculate_ema(prices, ema_slow)
        ema_t = calculate_ema(prices, ema_trend)
        atr = calculate_atr(highs, lows, prices, atr_period)
        vol_sma = calculate_sma(volumes, volume_sma_period)
        adx = calculate_adx(highs, lows, prices, adx_period)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0  # 0=flat, 1=long, -1=short
        entry_bar = 0
        entry_price = 0.0
        trailing_stop = 0.0  # ATR trailing stop level
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        # Need enough data for all indicators
        start_bar = max(ema_trend, roc_period, atr_period + 1, volume_sma_period)

        for i in range(start_bar, n):
            price = prices[i]

            # Skip if any indicator is NaN
            if (
                np.isnan(roc[i])
                or np.isnan(ema_f[i])
                or np.isnan(ema_s[i])
                or np.isnan(ema_t[i])
                or np.isnan(atr[i])
                or np.isnan(vol_sma[i])
            ):
                continue

            current_roc = roc[i]
            current_atr = atr[i]
            vol_ok = volumes[i] > vol_sma[i] * volume_threshold
            current_adx = adx[i] if not np.isnan(adx[i]) else 0.0
            is_trending = current_adx >= adx_trend_threshold

            # ---- 0. Regime filter: close positions in ranging market ----
            if not is_trending and position != 0:
                if i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                    continue

            # ---- 1. Exit / stop-loss checks ----
            if position != 0 and entry_price > 0:
                is_long = position == 1

                # Hard stop loss
                if is_long:
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price

                if pnl_pct < -stop_loss_pct:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                    continue

                # ATR trailing stop
                if is_long:
                    new_stop = price - current_atr * atr_multiplier
                    trailing_stop = max(trailing_stop, new_stop)
                    if price < trailing_stop:
                        signals[i] = Signal.CLOSE.value
                        position = 0
                        entry_price = 0.0
                        trailing_stop = 0.0
                        cooldown_until = i + cooldown_bars
                        continue
                else:
                    new_stop = price + current_atr * atr_multiplier
                    if trailing_stop == 0.0:
                        trailing_stop = new_stop
                    else:
                        trailing_stop = min(trailing_stop, new_stop)
                    if price > trailing_stop:
                        signals[i] = Signal.CLOSE.value
                        position = 0
                        entry_price = 0.0
                        trailing_stop = 0.0
                        cooldown_until = i + cooldown_bars
                        continue

                # Momentum/trend reversal exit
                if is_long:
                    if current_roc < 0 or ema_f[i] < ema_s[i]:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = Signal.CLOSE.value
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue
                else:
                    if current_roc > 0 or ema_f[i] > ema_s[i]:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = Signal.CLOSE.value
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue

            # ---- 2. Entry signal generation ----
            raw_signal = Signal.HOLD.value

            # Long entry conditions (regime filter: only enter in trending market)
            long_ok = (
                is_trending
                and current_roc > roc_threshold
                and ema_f[i] > ema_s[i]
                and price > ema_t[i]
                and vol_ok
            )
            # Short entry conditions (symmetric)
            short_ok = (
                is_trending
                and current_roc < -roc_threshold
                and ema_f[i] < ema_s[i]
                and price < ema_t[i]
                and vol_ok
            )

            if long_ok:
                raw_signal = Signal.BUY.value
            elif short_ok:
                raw_signal = Signal.SELL.value

            # ---- 3. Cooldown check ----
            if i < cooldown_until:
                continue

            # ---- 4. Signal confirmation ----
            if raw_signal == Signal.BUY.value:
                signal_count[Signal.BUY.value] += 1
                signal_count[Signal.SELL.value] = 0
            elif raw_signal == Signal.SELL.value:
                signal_count[Signal.SELL.value] += 1
                signal_count[Signal.BUY.value] = 0
            else:
                signal_count[Signal.BUY.value] = 0
                signal_count[Signal.SELL.value] = 0

            confirmed_signal = Signal.HOLD.value
            if signal_count[Signal.BUY.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.BUY.value
            elif signal_count[Signal.SELL.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.SELL.value

            # ---- 5. Position management ----
            if confirmed_signal == Signal.BUY.value:
                if position == -1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.BUY.value
                    position = 1
                    entry_bar = i
                    entry_price = price
                    trailing_stop = price - current_atr * atr_multiplier

            elif confirmed_signal == Signal.SELL.value:
                if position == 1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.SELL.value
                    position = -1
                    entry_bar = i
                    entry_price = price
                    trailing_stop = price + current_atr * atr_multiplier

        return signals
