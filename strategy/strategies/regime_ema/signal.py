"""
EMA Crossover + Regime Filter Signal Generator.

Exchange-agnostic signal generator for the unified backtest framework.

Logic:
1. Compute ATR, ADX, fast/slow EMA.
2. Classify market regime each bar.
3. Only allow new entries (BUY/SELL) in trending regimes.
4. Auto-CLOSE positions when switching to RANGING.
5. Apply standard stop-loss and trade filters.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.regime_ema.core import (
    MarketRegime,
    RegimeEMAConfig,
    calculate_adx,
    calculate_atr,
    calculate_ema,
    classify_regime,
)


@dataclass
class RegimeEMATradeFilterConfig:
    """Configuration for trade filtering (Regime-EMA specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class RegimeEMASignalGenerator:
    """Generate trading signals with regime filtering.

    Replicates the EMA crossover logic but adds ATR+ADX regime detection
    to suppress trades during ranging / low-volatility markets.
    """

    def __init__(
        self,
        config: RegimeEMAConfig,
        filter_config: RegimeEMATradeFilterConfig,
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
        # --- resolve parameters ------------------------------------------------
        p = params or {}
        fast_period = int(p.get("fast_period", self.config.fast_period))
        slow_period = int(p.get("slow_period", self.config.slow_period))
        atr_period = int(p.get("atr_period", self.config.atr_period))
        adx_period = int(p.get("adx_period", self.config.adx_period))
        regime_lookback = int(p.get("regime_lookback", self.config.regime_lookback))
        trend_atr_threshold = float(
            p.get("trend_atr_threshold", self.config.trend_atr_threshold)
        )
        ranging_atr_threshold = float(
            p.get("ranging_atr_threshold", self.config.ranging_atr_threshold)
        )
        adx_trend_threshold = float(
            p.get("adx_trend_threshold", self.config.adx_trend_threshold)
        )
        stop_loss_pct = float(p.get("stop_loss_pct", self.config.stop_loss_pct))

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values

        # --- compute indicators ------------------------------------------------
        fast_ema = calculate_ema(prices, fast_period)
        slow_ema = calculate_ema(prices, slow_period)
        atr = calculate_atr(highs, lows, prices, atr_period)
        adx = calculate_adx(highs, lows, prices, adx_period)

        # Rolling ATR mean for regime classification
        atr_mean = np.full(n, np.nan)
        for i in range(regime_lookback - 1, n):
            window = atr[i - regime_lookback + 1 : i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) > 0:
                atr_mean[i] = np.mean(valid)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0  # 1=long, -1=short, 0=flat
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        # Need all indicators to be valid
        start_bar = max(slow_period, atr_period + adx_period, regime_lookback) + 5

        for i in range(start_bar, n):
            price = prices[i]

            if (
                np.isnan(fast_ema[i])
                or np.isnan(slow_ema[i])
                or np.isnan(atr[i])
                or np.isnan(adx[i])
                or np.isnan(atr_mean[i])
            ):
                continue

            if np.isnan(fast_ema[i - 1]) or np.isnan(slow_ema[i - 1]):
                continue

            # --- classify regime -----------------------------------------------
            regime = classify_regime(
                atr_val=atr[i],
                atr_mean=atr_mean[i],
                adx_val=adx[i],
                fast_ema=fast_ema[i],
                slow_ema=slow_ema[i],
                trend_atr_threshold=trend_atr_threshold,
                ranging_atr_threshold=ranging_atr_threshold,
                adx_trend_threshold=adx_trend_threshold,
            )

            is_trending = regime in (
                MarketRegime.TRENDING_UP,
                MarketRegime.TRENDING_DOWN,
            )

            # --- regime filter: auto-close in ranging --------------------------
            if not is_trending and position != 0:
                if i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    entry_bar = i
                    cooldown_until = i + cooldown_bars
                continue  # skip further logic while ranging

            if not is_trending:
                # No position and not trending → do nothing
                signal_count[Signal.BUY.value] = 0
                signal_count[Signal.SELL.value] = 0
                continue

            # --- stop loss check -----------------------------------------------
            if position != 0 and entry_price > 0:
                is_long = position == 1
                if is_long:
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price

                if pnl_pct < -stop_loss_pct:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    entry_bar = i
                    cooldown_until = i + cooldown_bars
                    continue

            # --- EMA crossover signal ------------------------------------------
            prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
            curr_diff = fast_ema[i] - slow_ema[i]

            raw_signal = Signal.HOLD.value
            if prev_diff <= 0 and curr_diff > 0:
                raw_signal = Signal.BUY.value
            elif prev_diff >= 0 and curr_diff < 0:
                raw_signal = Signal.SELL.value

            # --- cooldown check ------------------------------------------------
            if i < cooldown_until:
                continue

            # --- signal confirmation -------------------------------------------
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

            # --- position management -------------------------------------------
            if confirmed_signal == Signal.BUY.value:
                if position == -1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.BUY.value
                    position = 1
                    entry_bar = i
                    entry_price = price

            elif confirmed_signal == Signal.SELL.value:
                if position == 1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.SELL.value
                    position = -1
                    entry_bar = i
                    entry_price = price

        return signals
