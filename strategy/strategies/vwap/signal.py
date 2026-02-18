"""
VWAP Mean Reversion Signal Generator.

Extracted to be exchange-agnostic for the unified backtest framework.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.vwap.core import (
    VWAPConfig,
    calculate_rsi,
    calculate_vwap,
    calculate_vwap_zscore,
)


@dataclass
class VWAPTradeFilterConfig:
    """Configuration for trade filtering (VWAP-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class VWAPSignalGenerator:
    """Generate trading signals for vectorized backtest.

    Implements VWAP mean-reversion logic:
    - Z <= -entry AND RSI < oversold -> BUY
    - Z >= entry AND RSI > overbought -> SELL
    - |Z| < exit_threshold -> CLOSE (price returned to VWAP)
    - |Z| > zscore_stop OR pnl < -stop_loss_pct -> CLOSE (model failure)
    """

    def __init__(self, config: VWAPConfig, filter_config: VWAPTradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame with columns: open, high, low, close, volume.
                  Index should be DatetimeIndex for daily VWAP reset.
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
        """
        std_window = int(
            params.get("std_window", self.config.std_window)
            if params
            else self.config.std_window
        )
        rsi_period = int(
            params.get("rsi_period", self.config.rsi_period)
            if params
            else self.config.rsi_period
        )
        zscore_entry = float(
            params.get("zscore_entry", self.config.zscore_entry)
            if params
            else self.config.zscore_entry
        )
        zscore_exit = float(
            params.get("zscore_exit", self.config.zscore_exit)
            if params
            else self.config.zscore_exit
        )
        zscore_stop = float(
            params.get("zscore_stop", self.config.zscore_stop)
            if params
            else self.config.zscore_stop
        )
        rsi_oversold = float(
            params.get("rsi_oversold", self.config.rsi_oversold)
            if params
            else self.config.rsi_oversold
        )
        rsi_overbought = float(
            params.get("rsi_overbought", self.config.rsi_overbought)
            if params
            else self.config.rsi_overbought
        )
        stop_loss_pct = float(
            params.get("stop_loss_pct", self.config.stop_loss_pct)
            if params
            else self.config.stop_loss_pct
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values
        volumes = data["volume"].values

        # Compute indicators
        timestamps = data.index if isinstance(data.index, pd.DatetimeIndex) else None
        vwap = calculate_vwap(highs, lows, prices, volumes, timestamps)
        zscore = calculate_vwap_zscore(prices, vwap, std_window)
        rsi = calculate_rsi(prices, rsi_period)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars

        position = 0
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        # Skip warmup: need std_window bars for valid Z-score
        start_bar = std_window

        for i in range(start_bar, n):
            price = prices[i]

            if np.isnan(zscore[i]) or np.isnan(rsi[i]):
                continue

            z = zscore[i]
            r = rsi[i]

            # 1. Stop loss check
            if position != 0 and entry_price > 0:
                is_long = position == 1
                if is_long:
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price

                # Z-score model failure OR price stop
                if abs(z) >= zscore_stop or pnl_pct < -stop_loss_pct:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    entry_bar = i
                    cooldown_until = i + cooldown_bars
                    continue

            # 2. Raw signal generation
            raw_signal = Signal.HOLD.value

            if z <= -zscore_entry and r < rsi_oversold:
                raw_signal = Signal.BUY.value
            elif z >= zscore_entry and r > rsi_overbought:
                raw_signal = Signal.SELL.value
            elif position != 0 and abs(z) <= abs(zscore_exit):
                raw_signal = Signal.CLOSE.value

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
