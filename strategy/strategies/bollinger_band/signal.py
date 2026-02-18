"""
Bollinger Band Signal Generator.

Extracted from strategy/bitget/bollinger_band/backtest.py to be exchange-agnostic.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.bollinger_band.core import BBConfig, calculate_bollinger_bands


@dataclass
class BBTradeFilterConfig:
    """Configuration for trade filtering (BB-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class BBSignalGenerator:
    """Generate trading signals for vectorized backtest.

    Replicates the Bollinger Band mean-reversion logic for backtesting:
    - Price <= lower band -> BUY (oversold)
    - Price >= upper band -> SELL (overbought)
    - Price returns near SMA -> CLOSE
    """

    def __init__(self, config: BBConfig, filter_config: BBTradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame.
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
        """
        bb_period = int(
            params.get("bb_period", self.config.bb_period)
            if params
            else self.config.bb_period
        )
        bb_multiplier = float(
            params.get("bb_multiplier", self.config.bb_multiplier)
            if params
            else self.config.bb_multiplier
        )
        exit_threshold = float(
            params.get("exit_threshold", self.config.exit_threshold)
            if params
            else self.config.exit_threshold
        )
        stop_loss_pct = float(
            params.get("stop_loss_pct", self.config.stop_loss_pct)
            if params
            else self.config.stop_loss_pct
        )
        trend_bias = (
            params.get("trend_bias", self.config.trend_bias)
            if params
            else self.config.trend_bias
        )
        trend_sma_mult = int(
            params.get("trend_sma_multiplier", self.config.trend_sma_multiplier)
            if params
            else self.config.trend_sma_multiplier
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        sma, upper, lower = calculate_bollinger_bands(prices, bb_period, bb_multiplier)

        # Calculate trend SMA for auto bias
        trend_sma_len = bb_period * trend_sma_mult
        trend_sma = np.full(n, np.nan)
        if trend_bias == "auto":
            for ti in range(trend_sma_len - 1, n):
                trend_sma[ti] = np.mean(prices[ti - trend_sma_len + 1 : ti + 1])

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        start_bar = bb_period - 1

        for i in range(start_bar, n):
            price = prices[i]

            if np.isnan(sma[i]) or np.isnan(upper[i]) or np.isnan(lower[i]):
                continue

            # 1. Stop loss check
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
                    continue

            # 2. Raw signal generation
            raw_signal = Signal.HOLD.value

            if price <= lower[i]:
                raw_signal = Signal.BUY.value
            elif price >= upper[i]:
                raw_signal = Signal.SELL.value
            elif position != 0:
                band_half = upper[i] - sma[i]
                if band_half > 1e-10:
                    distance_ratio = abs(price - sma[i]) / band_half
                    if distance_ratio < exit_threshold:
                        raw_signal = Signal.CLOSE.value

            # 2b. Apply trend bias filter
            effective_bias = trend_bias
            if trend_bias == "auto" and not np.isnan(trend_sma[i]):
                effective_bias = "short_only" if price < trend_sma[i] else "long_only"

            if effective_bias == "short_only" and raw_signal == Signal.BUY.value:
                raw_signal = Signal.HOLD.value
            elif effective_bias == "long_only" and raw_signal == Signal.SELL.value:
                raw_signal = Signal.HOLD.value

            # 3. Cooldown check
            if i < cooldown_until:
                if raw_signal == Signal.CLOSE.value and position != 0:
                    if i - entry_bar >= min_holding_bars:
                        signals[i] = Signal.CLOSE.value
                        position = 0
                        entry_price = 0.0
                        cooldown_until = i + cooldown_bars
                continue

            # 4. Signal confirmation
            if raw_signal == Signal.BUY.value:
                signal_count[Signal.BUY.value] += 1
                signal_count[Signal.SELL.value] = 0
            elif raw_signal == Signal.SELL.value:
                signal_count[Signal.SELL.value] += 1
                signal_count[Signal.BUY.value] = 0
            else:
                signal_count[Signal.BUY.value] = 0
                signal_count[Signal.SELL.value] = 0

            confirmed_signal = raw_signal
            if raw_signal in (Signal.BUY.value, Signal.SELL.value):
                if raw_signal == Signal.BUY.value:
                    if signal_count[Signal.BUY.value] < self.filter.signal_confirmation:
                        confirmed_signal = Signal.HOLD.value
                elif raw_signal == Signal.SELL.value:
                    if (
                        signal_count[Signal.SELL.value]
                        < self.filter.signal_confirmation
                    ):
                        confirmed_signal = Signal.HOLD.value

            # 5. Position management
            if confirmed_signal == Signal.CLOSE.value and position != 0:
                if i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown_bars

            elif confirmed_signal == Signal.BUY.value:
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
