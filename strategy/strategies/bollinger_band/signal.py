"""
Bollinger Band Signal Generator.

Exchange-agnostic signal generator for the backtest framework.
Delegates to BBSignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.bollinger_band import BBSignalCore
from strategy.strategies.bollinger_band.core import BBConfig


@dataclass
class BBTradeFilterConfig:
    """Configuration for trade filtering (BB-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class BBSignalGenerator:
    """Generate trading signals for vectorized backtest.

    Replicates the Bollinger Band mean-reversion logic for backtesting.
    Delegates to BBSignalCore for bar-by-bar signal generation.
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
        p = params or {}

        # Build effective config with parameter overrides
        bb_period = int(p.get("bb_period", self.config.bb_period))
        bb_multiplier = float(p.get("bb_multiplier", self.config.bb_multiplier))
        exit_threshold = float(p.get("exit_threshold", self.config.exit_threshold))
        stop_loss_pct = float(p.get("stop_loss_pct", self.config.stop_loss_pct))
        trend_bias = p.get("trend_bias", self.config.trend_bias)
        trend_sma_multiplier = int(
            p.get("trend_sma_multiplier", self.config.trend_sma_multiplier)
        )

        effective_config = BBConfig(
            bb_period=bb_period,
            bb_multiplier=bb_multiplier,
            exit_threshold=exit_threshold,
            stop_loss_pct=stop_loss_pct,
            trend_bias=trend_bias,
            trend_sma_multiplier=trend_sma_multiplier,
            symbols=self.config.symbols,
            timeframe=self.config.timeframe,
            position_size_pct=self.config.position_size_pct,
            daily_loss_limit=self.config.daily_loss_limit,
        )

        # Build effective filter config
        min_holding_bars = int(p.get("min_holding_bars", self.filter.min_holding_bars))
        cooldown_bars = int(p.get("cooldown_bars", self.filter.cooldown_bars))
        signal_confirmation = int(
            p.get("signal_confirmation", self.filter.signal_confirmation)
        )

        # Create core and process all bars
        core = BBSignalCore(
            config=effective_config,
            min_holding_bars=min_holding_bars,
            cooldown_bars=cooldown_bars,
            signal_confirmation=signal_confirmation,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        for i in range(n):
            signals[i] = core.update(close=prices[i])

        return signals
