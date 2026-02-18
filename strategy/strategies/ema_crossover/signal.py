"""
EMA Crossover Signal Generator.

Extracted from strategy/bitget/ema_crossover/backtest.py to be exchange-agnostic.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.ema_crossover.core import EMAConfig, calculate_ema


@dataclass
class EMATradeFilterConfig:
    """Configuration for trade filtering (EMA-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class EMASignalGenerator:
    """Generate trading signals for vectorized backtest.

    Replicates the logic from EMACrossoverIndicator for backtesting.
    """

    def __init__(self, config: EMAConfig, filter_config: EMATradeFilterConfig):
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
        fast_period = int(
            params.get("fast_period", self.config.fast_period)
            if params
            else self.config.fast_period
        )
        slow_period = int(
            params.get("slow_period", self.config.slow_period)
            if params
            else self.config.slow_period
        )
        stop_loss_pct = (
            params.get("stop_loss_pct", self.config.stop_loss_pct)
            if params
            else self.config.stop_loss_pct
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        fast_ema = calculate_ema(prices, fast_period)
        slow_ema = calculate_ema(prices, slow_period)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        start_bar = slow_period

        for i in range(start_bar, n):
            price = prices[i]

            if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]):
                continue
            if np.isnan(fast_ema[i - 1]) or np.isnan(slow_ema[i - 1]):
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

            # 2. Raw signal generation (crossover detection)
            prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
            curr_diff = fast_ema[i] - slow_ema[i]

            raw_signal = Signal.HOLD.value

            if prev_diff <= 0 and curr_diff > 0:
                raw_signal = Signal.BUY.value
            elif prev_diff >= 0 and curr_diff < 0:
                raw_signal = Signal.SELL.value

            # 3. Cooldown check
            if i < cooldown_until:
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

            confirmed_signal = Signal.HOLD.value
            if signal_count[Signal.BUY.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.BUY.value
            elif signal_count[Signal.SELL.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.SELL.value

            # 5. Position management
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
