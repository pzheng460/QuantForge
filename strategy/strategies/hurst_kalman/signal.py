"""
Hurst-Kalman Signal Generator.

Extracted from strategy/bitget/hurst_kalman/backtest.py to be exchange-agnostic.
"""

from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.hurst_kalman.core import (
    HurstKalmanConfig,
    KalmanFilter1D,
    calculate_hurst,
)


@dataclass
class TradeFilterConfig:
    """Configuration for trade filtering (Hurst-Kalman specific)."""

    min_holding_bars: int = 8
    cooldown_bars: int = 4
    signal_confirmation: int = 1
    only_mean_reversion: bool = True


class HurstKalmanSignalGenerator:
    """Generate trading signals for vectorized backtest.

    Replicates the logic from HurstKalmanIndicator for backtesting.
    """

    def __init__(self, config: HurstKalmanConfig, filter_config: TradeFilterConfig):
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
        hurst_window = (
            params.get("hurst_window", self.config.hurst_window)
            if params
            else self.config.hurst_window
        )
        zscore_window = (
            params.get("zscore_window", self.config.zscore_window)
            if params
            else self.config.zscore_window
        )
        zscore_entry = (
            params.get("zscore_entry", self.config.zscore_entry)
            if params
            else self.config.zscore_entry
        )
        mean_reversion_threshold = (
            params.get("mean_reversion_threshold", self.config.mean_reversion_threshold)
            if params
            else self.config.mean_reversion_threshold
        )
        trend_threshold = (
            params.get("trend_threshold", self.config.trend_threshold)
            if params
            else self.config.trend_threshold
        )
        kalman_R = (
            params.get("kalman_R", self.config.kalman_R)
            if params
            else self.config.kalman_R
        )
        kalman_Q = (
            params.get("kalman_Q", self.config.kalman_Q)
            if params
            else self.config.kalman_Q
        )
        stop_loss_pct = (
            params.get("stop_loss_pct", self.config.stop_loss_pct)
            if params
            else self.config.stop_loss_pct
        )
        zscore_stop = (
            params.get("zscore_stop", self.config.zscore_stop)
            if params
            else self.config.zscore_stop
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        kalman = KalmanFilter1D(R=kalman_R, Q=kalman_Q)
        kalman_prices = []
        price_history = deque(maxlen=hurst_window + 50)

        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars
        only_mean_reversion = self.filter.only_mean_reversion

        position = 0
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}
        last_trending_signal = Signal.HOLD.value

        for i in range(n):
            price = prices[i]
            price_history.append(price)

            kalman_price = kalman.update(price)
            kalman_prices.append(kalman_price)

            if i < hurst_window + zscore_window:
                continue

            hurst = calculate_hurst(np.array(price_history), hurst_window)

            recent_prices = np.array(list(price_history)[-zscore_window:])
            recent_kalman = np.array(kalman_prices[-zscore_window:])
            deviations = recent_prices - recent_kalman
            std = np.std(deviations)
            zscore = (price - kalman_price) / std if std > 1e-10 else 0.0

            slope = kalman.get_slope(lookback=5)

            # 1. Stop loss check
            if position != 0 and entry_price > 0:
                is_long = position == 1
                if is_long:
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price

                stop_triggered = False
                if pnl_pct < -stop_loss_pct:
                    stop_triggered = True
                if abs(zscore) > zscore_stop:
                    stop_triggered = True

                if stop_triggered:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    entry_bar = i
                    continue

            # 2. Market classification
            if hurst < mean_reversion_threshold:
                market_state = "mean_reverting"
            elif hurst > trend_threshold:
                market_state = "trending"
            else:
                market_state = "random_walk"

            # 3. Raw signal generation
            raw_signal = Signal.HOLD.value

            if market_state == "random_walk":
                raw_signal = Signal.CLOSE.value
            elif market_state == "mean_reverting":
                if zscore < -zscore_entry:
                    raw_signal = Signal.BUY.value
                elif zscore > zscore_entry:
                    raw_signal = Signal.SELL.value
                elif abs(zscore) < 0.5:
                    raw_signal = Signal.CLOSE.value
                else:
                    raw_signal = Signal.HOLD.value
            elif market_state == "trending":
                if price > kalman_price and slope > 0:
                    raw_signal = Signal.BUY.value
                elif price < kalman_price and slope < 0:
                    raw_signal = Signal.SELL.value
                elif (
                    slope * (1 if last_trending_signal == Signal.BUY.value else -1) < 0
                ):
                    raw_signal = Signal.CLOSE.value
                else:
                    raw_signal = Signal.HOLD.value
                if raw_signal in (Signal.BUY.value, Signal.SELL.value):
                    last_trending_signal = raw_signal

            # 4. only_mean_reversion filter
            if only_mean_reversion and market_state != "mean_reverting":
                if raw_signal in (Signal.BUY.value, Signal.SELL.value):
                    raw_signal = Signal.HOLD.value
                if raw_signal == Signal.CLOSE.value or (
                    position != 0 and market_state != "mean_reverting"
                ):
                    if position != 0 and i - entry_bar >= min_holding_bars:
                        signals[i] = Signal.CLOSE.value
                        position = 0
                        entry_price = 0.0
                        cooldown_until = i + cooldown_bars
                    continue
                if raw_signal == Signal.HOLD.value:
                    continue

            # 5. Cooldown check
            if i < cooldown_until:
                continue

            # 6. Signal confirmation
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
            elif raw_signal == Signal.CLOSE.value:
                confirmed_signal = Signal.CLOSE.value

            # 7. Position management
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

            elif confirmed_signal == Signal.CLOSE.value:
                if position != 0 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown_bars

        return signals
