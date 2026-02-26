"""
Dual Regime Signal Generator.

Exchange-agnostic vectorised signal generator that switches between:
- Momentum strategy (ADX >= threshold): ROC + EMA + Volume confirmation
- Bollinger Band mean reversion (ADX < threshold): Price touch bands with middle band exit

Regime switching causes position closure before new strategy activation.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.dual_regime.core import (
    DualRegimeConfig,
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_roc,
    calculate_sma,
    calculate_bollinger_bands,
)


@dataclass
class DualRegimeTradeFilterConfig:
    """Configuration for trade filtering (Dual Regime specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class DualRegimeSignalGenerator:
    """Generate trading signals for dual regime backtest.

    Momentum Strategy (ADX >= threshold):
        Entry (long): ROC > threshold + EMA_fast > EMA_slow + Price > EMA_trend + Volume confirm
        Entry (short): ROC < -threshold + EMA_fast < EMA_slow + Price < EMA_trend + Volume confirm
        Exit: ROC reversal or EMA crossover reversal or ATR trailing stop

    Bollinger Band Strategy (ADX < threshold):
        Entry (long): Price < lower_band
        Entry (short): Price > upper_band
        Exit: Price crosses middle_band

    Regime Switch:
        Close all positions when ADX crosses threshold in either direction
    """

    def __init__(
        self,
        config: DualRegimeConfig,
        filter_config: DualRegimeTradeFilterConfig,
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

        # Parse parameters
        adx_period = int(p.get("adx_period", self.config.adx_period))
        adx_trend_threshold = float(p.get("adx_trend_threshold", self.config.adx_trend_threshold))
        
        # Momentum parameters
        roc_period = int(p.get("roc_period", self.config.roc_period))
        roc_threshold = float(p.get("roc_threshold", self.config.roc_threshold))
        ema_fast = int(p.get("ema_fast", self.config.ema_fast))
        ema_slow = int(p.get("ema_slow", self.config.ema_slow))
        ema_trend = int(p.get("ema_trend", self.config.ema_trend))
        atr_period = int(p.get("atr_period", self.config.atr_period))
        atr_multiplier = float(p.get("atr_multiplier", self.config.atr_multiplier))
        volume_sma_period = int(p.get("volume_sma_period", self.config.volume_sma_period))
        volume_threshold = float(p.get("volume_threshold", self.config.volume_threshold))
        
        # Bollinger Band parameters
        bb_period = int(p.get("bb_period", self.config.bb_period))
        bb_std = float(p.get("bb_std", self.config.bb_std))
        
        # Risk management
        stop_loss_pct = float(p.get("stop_loss_pct", self.config.stop_loss_pct))

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values
        volumes = data["volume"].values

        # Compute all indicators
        adx = calculate_adx(highs, lows, prices, adx_period)
        
        # Momentum indicators
        roc = calculate_roc(prices, roc_period)
        ema_f = calculate_ema(prices, ema_fast)
        ema_s = calculate_ema(prices, ema_slow)
        ema_t = calculate_ema(prices, ema_trend)
        atr = calculate_atr(highs, lows, prices, atr_period)
        vol_sma = calculate_sma(volumes, volume_sma_period)
        
        # Bollinger Band indicators
        bb_middle, bb_upper, bb_lower = calculate_bollinger_bands(prices, bb_period, bb_std)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0  # 0=flat, 1=long, -1=short
        entry_bar = 0
        entry_price = 0.0
        trailing_stop = 0.0  # ATR trailing stop level for momentum
        cooldown_until = 0
        current_regime = None  # 'momentum' or 'bb'
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        # Need enough data for all indicators
        start_bar = max(
            adx_period * 2 + 1,  # ADX needs 2*period+1
            ema_trend,
            roc_period,
            atr_period + 1,
            volume_sma_period,
            bb_period
        )

        for i in range(start_bar, n):
            price = prices[i]

            # Skip if any critical indicator is NaN
            if np.isnan(adx[i]) or np.isnan(bb_middle[i]):
                continue

            current_adx = adx[i]
            new_regime = 'momentum' if current_adx >= adx_trend_threshold else 'bb'

            # ---- 1. Regime Switch Detection ----
            if current_regime is not None and current_regime != new_regime:
                # Regime changed - close position if holding and enter cooldown
                if position != 0:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                current_regime = new_regime
                continue  # Skip entry signals on regime switch bars

            current_regime = new_regime

            # ---- 2. Exit / stop-loss checks ----
            if position != 0 and entry_price > 0:
                is_long = position == 1

                # Hard stop loss (shared by both strategies)
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

                # Strategy-specific exits
                if current_regime == 'momentum':
                    # Skip if momentum indicators are NaN
                    if (np.isnan(roc[i]) or np.isnan(ema_f[i]) or 
                        np.isnan(ema_s[i]) or np.isnan(atr[i])):
                        continue

                    current_atr = atr[i]

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
                    current_roc = roc[i]
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

                elif current_regime == 'bb':
                    # Bollinger Band exit: price crosses middle band
                    middle = bb_middle[i]
                    if is_long and price >= middle:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = Signal.CLOSE.value
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue
                    elif not is_long and price <= middle:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = Signal.CLOSE.value
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue

            # ---- 3. Entry signal generation ----
            raw_signal = Signal.HOLD.value

            if current_regime == 'momentum':
                # Skip if momentum indicators are NaN
                if (np.isnan(roc[i]) or np.isnan(ema_f[i]) or np.isnan(ema_s[i]) or 
                    np.isnan(ema_t[i]) or np.isnan(vol_sma[i])):
                    continue

                current_roc = roc[i]
                vol_ok = volumes[i] > vol_sma[i] * volume_threshold

                # Long entry conditions
                long_ok = (
                    current_roc > roc_threshold
                    and ema_f[i] > ema_s[i]
                    and price > ema_t[i]
                    and vol_ok
                )
                # Short entry conditions
                short_ok = (
                    current_roc < -roc_threshold
                    and ema_f[i] < ema_s[i]
                    and price < ema_t[i]
                    and vol_ok
                )

                if long_ok:
                    raw_signal = Signal.BUY.value
                elif short_ok:
                    raw_signal = Signal.SELL.value

            elif current_regime == 'bb':
                # Bollinger Band entry conditions
                upper = bb_upper[i]
                lower = bb_lower[i]

                if price < lower:
                    raw_signal = Signal.BUY.value
                elif price > upper:
                    raw_signal = Signal.SELL.value

            # ---- 4. Cooldown check ----
            if i < cooldown_until:
                continue

            # ---- 5. Signal confirmation ----
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

            # ---- 6. Position management ----
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
                    if current_regime == 'momentum' and not np.isnan(atr[i]):
                        trailing_stop = price - atr[i] * atr_multiplier
                    else:
                        trailing_stop = 0.0

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
                    if current_regime == 'momentum' and not np.isnan(atr[i]):
                        trailing_stop = price + atr[i] * atr_multiplier
                    else:
                        trailing_stop = 0.0

        return signals